"""Deterministic checks (terms, voice phrases, accessibility, channel format) and
the LLM voice judge. Deterministic checks are exact and repeatable; the LLM
judge only adds tone findings, capped at 'warn', and each must cite a voice rule."""
from __future__ import annotations

import re
from typing import Any

from ..llm import gateway
from ..llm.gateway import ARR, OBJ, S
from .core import Draft, RunContext
from .text import emoji_spans, find_phrases, fk_grade, match_case, phrase_regex, sentences

# ------------------------------------------------------------------ terms


def check_terms(ctx: RunContext) -> list[Draft]:
    out: list[Draft] = []
    text = ctx.text
    for r in ctx.rules_of("term"):
        p = r["params"]
        for s in find_phrases(text, p.get("patterns", []), p.get("case_sensitive", False)):
            rep = p.get("replacement")
            if p.get("mode") == "preferred":
                if rep and s.text == rep:
                    continue
                title = f"Use “{rep}”" if rep else r["title"]
                msg = f"“{s.text}” should be written “{rep}”." if rep else r["title"]
            else:
                title = f"“{s.text}” is not permitted"
                msg = r["title"] + "."
            out.append(Draft(check="terms", severity=r["severity"], rule_key=r["key"], title=title, message=msg,
                             start=s.start, end=s.end, fix=(match_case(rep, s.text) if rep else None)))
    return out


# ------------------------------------------------------------------ voice


def check_voice_phrases(ctx: RunContext) -> list[Draft]:
    out: list[Draft] = []
    for r in ctx.rules_of("voice"):
        p = r["params"]
        reps = {k.lower(): v for k, v in (p.get("replacements") or {}).items()}
        for s in find_phrases(ctx.text, p.get("flag_phrases", []), False):
            rep = reps.get(s.text.lower().replace("’", "'"))
            out.append(Draft(check="voice", severity=_cap(r["severity"]), rule_key=r["key"],
                             title=f"Off-voice: “{s.text}”", message=f"Breaks {r['section']} “{p.get('trait', r['title'])}”.",
                             start=s.start, end=s.end, fix=(match_case(rep, s.text) if rep else None)))
    return out


def _cap(sev: str) -> str:
    # Voice is a judgment call: it can never block publication on its own.
    return "warn" if sev == "block" else sev


VOICE_SCHEMA = OBJ({
    "issues": ARR(OBJ({
        "rule_key": S("string"),
        "span": S("string", description="exact substring copied from the content"),
        "reason": S("string"),
        "suggestion": S("string", description="minimal replacement for the span that keeps the creator's meaning; empty if none"),
        "confidence": S("number"),
    })),
})

VOICE_SYSTEM = (
    "You are the brand-voice reviewer inside Plumb, a brand governance tool. "
    "You check content ONLY against the voice rules provided. Report a span only when it clearly breaks a rule, "
    "judged by the rule's own good and bad examples -- not by general taste. Do not flag factual claims, spelling, "
    "or anything the rules do not cover. Copy each span exactly from the content (same characters). "
    "Suggestions must be minimal edits of the span that keep the creator's goal and call to action. "
    "Accept Indian English. If nothing breaks a rule, return an empty list."
)


def check_voice_llm(ctx: RunContext, existing: list[Draft]) -> list[Draft]:
    rules = ctx.rules_of("voice")
    if not rules or not ctx.use_llm:
        return []
    import json

    payload = {
        "voice_rules": [{"rule_key": r["key"], "rule": r["params"].get("trait", r["title"]), "guideline_text": r["source_quote"],
                         "good_examples": r["params"].get("good", []), "bad_examples": r["params"].get("bad", [])} for r in rules],
        "intent": ctx.intent,
        "content": ctx.text,
    }
    res = gateway.call(gateway.Ctx("voice", **ctx.llm_ctx), VOICE_SYSTEM, json.dumps(payload, ensure_ascii=False), VOICE_SCHEMA)
    if not res:
        return []
    keys = {r["key"]: r for r in rules}
    out: list[Draft] = []
    for it in res.get("issues", [])[:20]:
        r = keys.get(str(it.get("rule_key", "")).strip())
        span = str(it.get("span", ""))
        if not r or not span.strip():
            continue
        idx = ctx.text.find(span)
        if idx < 0:
            continue  # model quoted text that isn't there -> discard
        a, b = idx, idx + len(span)
        if any(d.overlaps(a, b) and d.check == "voice" for d in existing + out):
            continue
        conf = float(it.get("confidence") or 0.6)
        conf = max(0.0, min(1.0, conf))
        sugg = str(it.get("suggestion") or "").strip() or None
        sev = "warn" if conf >= 0.6 else "suggest"
        out.append(Draft(check="voice", severity=min(_cap(r["severity"]), sev, key=lambda s: {"warn": 2, "suggest": 1}.get(s, 0)),
                         rule_key=r["key"], title=f"Tone: “{span[:60]}”", message=str(it.get("reason", ""))[:400],
                         start=a, end=b, fix=sugg, confidence=conf, source="llm",
                         extra={"low_confidence": conf < 0.6}))
    return out


# ------------------------------------------------------------------ accessibility

_CAPS = re.compile(r"(?<![\w#@])[A-Z][A-Z'’]{2,}(?![\w])")
_MD_IMG = re.compile(r"!\[(.*?)\]\((.*?)\)")
_HTML_IMG = re.compile(r"<img\b(?![^>]*\balt\s*=\s*\"[^\"]+\")[^>]*>", re.IGNORECASE)
_CLICK_HERE_TO = re.compile(r"\b(?:click|tap)\s+here\s+to\s+(\w+)", re.IGNORECASE)


def check_a11y(ctx: RunContext, term_findings: list[Draft]) -> list[Draft]:
    out: list[Draft] = []
    text = ctx.text
    sents = sentences(text)
    for r in ctx.rules_of("a11y"):
        p = r["params"]
        m = p.get("metric")
        if m == "reading_grade":
            words = len(re.findall(r"\w+", text))
            if words < 30:
                continue
            grade = fk_grade(text)
            if grade > float(p.get("max", 9)):
                hardest = max(sents, key=lambda s: fk_grade(s.text) if len(s.text.split()) > 4 else -99)
                out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"Reading grade {grade:.1f} (target ≤ {p.get('max', 9)})",
                                 message="Hardest sentence highlighted. Shorter sentences and plainer words help every reader.",
                                 start=hardest.start, end=hardest.end, extra={"grade": round(grade, 1)}))
        elif m == "all_caps":
            allow = {a.upper() for a in p.get("allow", [])}
            for mm in _CAPS.finditer(text):
                w = mm.group(0)
                if w.upper().strip("'’") in allow:
                    continue
                if any(t.overlaps(mm.start(), mm.end()) for t in term_findings):
                    continue
                at_start = any(s.start == mm.start() for s in sents) or text[max(0, mm.start() - 2): mm.start()].strip() in {"", "\n"}
                fix = w.capitalize() if at_start else w.lower()
                out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"ALL-CAPS “{w}”",
                                 message="Screen readers may spell out capitalised words letter by letter.",
                                 start=mm.start(), end=mm.end(), fix=fix))
        elif m == "exclamations":
            allowed = int(p.get("max", 1))
            used = 0
            for mm in re.finditer(r"!+", text):
                n = len(mm.group(0))
                if used + n <= allowed:
                    used += n
                    continue
                keep = max(0, allowed - used)
                used += n
                out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title="Too many exclamation marks",
                                 message=f"The guideline allows {allowed} per piece.", start=mm.start(), end=mm.end(),
                                 fix="!" * keep if keep else "."))
        elif m == "link_text":
            for s in find_phrases(text, p.get("phrases", []), False):
                fix = None
                mm = _CLICK_HERE_TO.match(text, s.start)
                if mm and mm.start() == s.start:
                    verb = mm.group(1)
                    out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"Vague link text “{s.text}”",
                                     message="Link text should say where it goes.", start=mm.start(), end=mm.end(),
                                     fix=verb[:1].upper() + verb[1:]))
                    continue
                out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"Vague link text “{s.text}”",
                                 message="Link text should say where it goes.", start=s.start, end=s.end, fix=fix))
        elif m == "alt_text":
            for mm in _MD_IMG.finditer(text):
                if not mm.group(1).strip():
                    out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title="Image without alt text",
                                     message="Describe the image for people using screen readers.", start=mm.start(), end=mm.end()))
            for mm in _HTML_IMG.finditer(text):
                out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title="Image without alt text",
                                 message="Describe the image for people using screen readers.", start=mm.start(), end=mm.end()))
        elif m == "emoji":
            allowed = int(p.get("max", 3))
            em = emoji_spans(text)
            if len(em) > allowed:
                extra = em[allowed:]
                # group contiguous extras into one finding
                a, b = extra[0].start, extra[-1].end
                if all(text[x.end:y.start].strip() == "" for x, y in zip(extra, extra[1:])):
                    out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"{len(em)} emoji (max {allowed})",
                                     message="Screen readers read every emoji aloud.", start=a, end=b, fix=""))
                else:
                    out.append(Draft(check="a11y", severity=r["severity"], rule_key=r["key"], title=f"{len(em)} emoji (max {allowed})",
                                     message="Screen readers read every emoji aloud.", start=extra[0].start, end=extra[0].end, fix=""))
    return out


# ------------------------------------------------------------------ channel format


def check_format(ctx: RunContext) -> list[Draft]:
    out: list[Draft] = []
    text = ctx.text
    for r in ctx.rules_of("format"):
        p = r["params"]
        kind = p.get("kind")
        if kind == "required_any":
            if p.get("author_types") and ctx.author_type not in p["author_types"]:
                continue
            if p.get("channels") and ctx.channel not in p["channels"]:
                continue
            req = p.get("required_any", [])
            if any(phrase_regex(t).search(text) for t in req):
                continue
            n = len(text.rstrip())
            sugg = p.get("suggestion") or (req[0] if req else None)
            out.append(Draft(check="format", severity=r["severity"], rule_key=r["key"], title=f"Missing {' / '.join(req[:3])}",
                             message=r["title"] + ".", start=n, end=n, fix=(" " + sugg) if sugg else None,
                             extra={"insertion": True}))
        elif kind == "max_chars":
            limit = (p.get("limits") or {}).get(ctx.channel)
            if limit and len(text) > int(limit):
                out.append(Draft(check="format", severity=r["severity"], rule_key=r["key"],
                                 title=f"{len(text):,} characters (limit {int(limit):,} on {ctx.channel})",
                                 message="Text past the limit is highlighted.", start=int(limit), end=len(text)))
    return out


def run_deterministic(ctx: RunContext) -> list[Draft]:
    terms = check_terms(ctx)
    voice = check_voice_phrases(ctx)
    return terms + voice + check_a11y(ctx, terms + voice) + check_format(ctx)


def describe_rule(r: dict[str, Any]) -> str:
    return f"{r['section']} · {r['key']}"
