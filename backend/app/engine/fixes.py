"""Intent-locked minimal fixes.

Every suggested fix is applied in a sandbox and the draft is re-checked:
  - the original finding must disappear,
  - no new blocking finding may appear where the fix landed,
  - the intent lock (must-keep phrases, call to action) must still hold.
Only fixes that pass are marked verified. An LLM may propose a better-worded
replacement for claim findings, but it goes through exactly the same test and
loses to the deterministic fix if it fails."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Optional

from ..llm import gateway
from ..llm.gateway import ARR, OBJ, S
from . import claims as claims_mod
from . import intent as intent_mod
from .checks import run_deterministic
from .core import Draft, RunContext
from .text import apply_edits

POLISH_SCHEMA = OBJ({"fixes": ARR(OBJ({"id": S("integer"), "replacement": S("string")}))})
POLISH_SYSTEM = (
    "You write minimal replacements for flagged spans in brand content. Replace ONLY the span. Keep the creator's goal, "
    "tone and call to action. When an approved fact is given, the replacement must say what the fact says -- no more. "
    "Prefer the approved wording. For medical claims, describe how the product feels or what it contains instead. "
    "The replacement must read naturally in the sentence. Return an empty string to remove the span."
)


def _recheck(ctx: RunContext, new_text: str, need_claims: bool) -> list[Draft]:
    c2 = replace(ctx, text=new_text, use_llm=False)
    out = run_deterministic(c2)
    if need_claims:
        cl = claims_mod.extract_heuristic(c2)
        claims_mod.verify(c2, cl)
        out += claims_mod.to_findings(c2, cl)
    return out


def verify_fix(ctx: RunContext, d: Draft, fix: str, original: list[Draft]) -> tuple[bool, str]:
    new_text, skipped = apply_edits(ctx.text, [(d.start, d.end, fix)])
    if skipped:
        return False, "Could not apply."
    lo = max(0, d.start - 2)
    hi = d.start + len(fix) + 2
    after = _recheck(ctx, new_text, need_claims=(d.check == "claims" or any(ch.isdigit() for ch in fix)))
    same = [f for f in after if f.rule_key == d.rule_key and (f.fact_key == d.fact_key or d.check != "claims")
            and (f.overlaps(lo, hi) or d.check == "format")]
    if same:
        return False, "The suggested wording still breaks the same rule."
    before_keys = {(f.rule_key, ctx.text[f.start:f.end].lower()) for f in original}
    new_blocks = [f for f in after if f.severity == "block" and f.overlaps(lo, hi) and (f.rule_key, new_text[f.start:f.end].lower()) not in before_keys]
    if new_blocks:
        b = new_blocks[0]
        return False, f"Creates a new issue ({b.rule_key}: {b.title})."
    ok, why = intent_mod.guard(ctx, ctx.text, new_text)
    if not ok:
        return False, why
    return True, ""


def _polish(ctx: RunContext, drafts: list[Draft]) -> dict[int, str]:
    items = []
    for i, d in enumerate(drafts):
        if d.check != "claims" or (d.extra.get("fix_mode") == "number"):
            continue
        f = ctx.fact(d.extra.get("replacement_fact") or d.fact_key or d.extra.get("suggested_fact"))
        sent = next((s for s in [ctx.text[max(0, d.start - 160): d.end + 160]]), "")
        items.append({"id": i, "context": sent, "span": ctx.text[d.start:d.end], "problem": d.title + ". " + d.message,
                      "approved_fact": (f.statement if f else ""), "approved_wording": (f.approved_phrasing if f else "")})
    if not items or not ctx.use_llm:
        return {}
    res = gateway.call(gateway.Ctx("fix", **ctx.llm_ctx), POLISH_SYSTEM,
                       json.dumps({"intent": ctx.intent, "items": items}, ensure_ascii=False), POLISH_SCHEMA)
    out: dict[int, str] = {}
    for it in (res or {}).get("fixes", []):
        try:
            out[int(it["id"])] = str(it.get("replacement", ""))[:300]
        except (KeyError, TypeError, ValueError):
            continue
    return out


def verify_all(ctx: RunContext, drafts: list[Draft]) -> None:
    polished = _polish(ctx, drafts)
    for i, d in enumerate(drafts):
        candidates: list[tuple[Optional[str], str]] = []
        if i in polished:
            candidates.append((polished[i], "llm"))
        if d.fix is not None:
            candidates.append((d.fix, "rule"))
        if not candidates:
            d.fix_note = d.fix_note or "No safe automatic fix — edit the text, or dispute the finding."
            continue
        last_note = ""
        chosen = False
        for fix, origin in candidates:
            if fix is None:
                continue
            ok, note = verify_fix(ctx, d, fix, drafts)
            if ok:
                d.fix, d.fix_verified = fix, True
                d.extra["fix_origin"] = origin
                d.fix_note = ("Removes the phrase. " if fix == "" else "") + ("AI-worded, re-checked against the policy and your intent." if origin == "llm" else "Re-checked against the policy and your intent.")
                chosen = True
                break
            last_note = note
        if not chosen:
            d.fix_verified = False
            d.fix_note = f"Suggestion not verified: {last_note}" if last_note else "Suggestion not verified."
            if d.fix is None and candidates and candidates[0][0] is not None:
                d.fix = candidates[0][0]
