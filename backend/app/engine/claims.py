"""Claims & Facts Ledger verification.

Pipeline per draft:
  1. extract claims   -- deterministic triggers + quantities (always), plus LLM extraction when available
  2. retrieve facts   -- sentence-embedding similarity + subject match + quantity-context overlap
  3. verify           -- exact numeric comparison where a number is involved (prices, %, counts, lakh/crore),
                         DeBERTa-v3 NLI entailment otherwise, LLM judge only for the ambiguous remainder
  4. verdict          -- supported | contradicted | expired | unsupported | banned, mapped to a rule + fact citation
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from ..llm import gateway
from ..llm.gateway import ARR, OBJ, S
from . import ml
from .core import Draft, FactView, RunContext
from .text import (
    Quantity,
    Span,
    clauses,
    containing,
    find_phrases,
    format_inr,
    phrase_regex,
    quantities,
    sentences,
    units_compatible,
)

CLASS_PRIORITY = ["health", "superlative", "environmental", "price", "statistic", "general"]
NUMERIC_CLASSES = {"price", "statistic"}
_VERBS = re.compile(r"\b(is|are|was|has|have|gives|give|provides|provide|ships|ship|comes|come|contains|contain|offers|offer|made|helps|help|keeps|keep|cures|cured|heals|treats|works|lasts|delivers|trust|trusts|serves|served|uses|includes)\b", re.I)
_QUAL = re.compile(r"(?:\b(?:over|more than|up to|upto|nearly|almost|around|about|at least|just|only)\s+)$", re.I)


@dataclass
class Claim:
    start: int
    end: int
    text: str
    claim_class: str
    hypothesis: str
    subject: str
    sentence: Span
    quantities: list[Quantity] = field(default_factory=list)
    source: str = "heuristic"
    banned_hits: list[str] = field(default_factory=list)
    clause: str = ""
    # verification results
    verdict: str = "unsupported"
    fact_key: Optional[str] = None
    replacement_fact: Optional[str] = None
    method: str = ""
    score: float = 0.0
    nli: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    conflict_q: Optional[Quantity] = None

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text, "claim_class": self.claim_class, "hypothesis": self.hypothesis,
                "verdict": self.verdict, "fact_key": self.fact_key, "replacement_fact": self.replacement_fact, "method": self.method,
                "score": round(self.score, 3), "source": self.source, "nli": {k: round(v, 3) for k, v in self.nli.items()},
                "rationale": self.rationale}


# ------------------------------------------------------------------ subjects


def _subjects_in(ctx: RunContext, text: str) -> list[tuple[int, str]]:
    canon = ctx.canonicalize(text)
    hits: list[tuple[int, str]] = []
    for name in ctx.products():
        for m in phrase_regex(name).finditer(canon):
            hits.append((m.start(), name))
    # aliases in the raw text map to canonical names too
    for r in ctx.rules_of("term"):
        p = r["params"]
        if p.get("mode") == "preferred" and p.get("replacement"):
            for s in find_phrases(text, p.get("patterns", []), p.get("case_sensitive", False)):
                hits.append((s.start, p["replacement"]))
    return sorted(set(hits))


def _brand(ctx: RunContext) -> str:
    rep = next((r["params"].get("replacement") for r in ctx.rules_of("term") if r["key"] == "T-1"), None)
    return rep or "The brand"


def _hypothesis(subject: str, clause_text: str, ctx: RunContext) -> str:
    c = ctx.canonicalize(clause_text).strip().rstrip(".!?")
    c = re.sub(r"^(?:meet\s+)?(?:the\s+new|our|my|it|its|this|that)\s+", "", c, flags=re.I)
    if subject.lower() in c.lower():
        return c
    if _VERBS.search(c):
        c = re.sub(r"^(?:it|this|which|that|and)\s+", "", c, flags=re.I)
        return f"{subject} {c}"
    return f"{subject} is {c}"


# ------------------------------------------------------------------ extraction


def _extend_superlative(text: str, a: int, b: int) -> tuple[int, int]:
    m = re.search(r"(\b[\w]+['’]s\s+)$", text[:a])
    if m:
        a = m.start()
    m = re.match(r"(\s+[\w]+-[\w-]+)", text[b:])
    if m:
        b += m.end()
    return a, b


def _quantity_span(text: str, q: Quantity) -> tuple[int, int]:
    """Highlight just the number (and its qualifier) -- fixes replace the number only."""
    return q.start, q.end


def extract_heuristic(ctx: RunContext) -> list[Claim]:
    text = ctx.text
    out: list[Claim] = []
    last_subject = _brand(ctx)
    trig_rules = {c: ctx.claim_rule(c) for c in ("health", "superlative", "environmental")}
    for s in sentences(text):
        subs = _subjects_in(ctx, s.text)
        sent_subject = subs[0][1] if subs else last_subject
        hits: list[tuple[int, int, str, str]] = []
        for cls, r in trig_rules.items():
            if not r:
                continue
            for sp in find_phrases(s.text, r["params"].get("triggers", []), False):
                a, b = s.start + sp.start, s.start + sp.end
                if cls == "superlative":
                    a, b = _extend_superlative(text, a, b)
                hits.append((a, b, cls, sp.text))
        qs = [Quantity(q.value, q.kind, q.unit, q.start + s.start, q.end + s.start, q.raw) for q in quantities(s.text)]
        for c in clauses(s):
            ch = [h for h in hits if c.start <= h[0] < c.end]
            cq = [q for q in qs if c.start <= q.start < c.end]
            if not ch and not cq:
                continue
            classes = [h[2] for h in ch]
            off = bool(re.search(r"%\s*off\b|\boff\b|\bdiscount|\bcashback|\bcode\b", c.text, re.I))
            for q in cq:
                classes.append("price" if q.kind == "inr" or (q.kind == "pct" and off) else "statistic")
            cls = next(k for k in CLASS_PRIORITY if k in classes)
            subj_here = [n for p, n in subs if s.start + p <= c.end]
            subject = subj_here[-1] if subj_here else sent_subject
            if cls in NUMERIC_CLASSES and cq:
                q = cq[0]
                a, b = _quantity_span(text, q)
            elif ch:
                a = min(h[0] for h in ch if h[2] == cls)
                b = max(h[1] for h in ch if h[2] == cls)
                for q in cq:  # "100% natural": include the number
                    if q.end <= a and a - q.end <= 2:
                        a = q.start
            else:
                a, b = c.start, c.end
            banned = [h[3] for h in ch if h[2] == "health"]
            if cls == "health":
                tail = text[a:c.end]
                tail = re.sub(r"[\s\U0001F300-\U0001FAFF\u2728\u2764\u2600-\u27BF!.?]+$", "", tail)
                b = max(b, a + len(tail))
            out.append(Claim(a, b, text[a:b], cls, _hypothesis(subject, c.text, ctx), subject, s, cq, "heuristic", banned, c.text))
        if subs:
            last_subject = subs[-1][1]
    return _dedupe(out)


def _dedupe(claims: list[Claim]) -> list[Claim]:
    claims.sort(key=lambda c: (c.start, -(c.end - c.start)))
    out: list[Claim] = []
    for c in claims:
        if out and c.start < out[-1].end:
            continue
        out.append(c)
    return out


EXTRACT_SCHEMA = OBJ({
    "claims": ARR(OBJ({
        "span": S("string", description="exact substring of the content that states the claim; as short as possible"),
        "claim": S("string", description="the claim rewritten as one standalone sentence naming its subject"),
        "claim_class": S("string", enum=["environmental", "health", "superlative", "price", "statistic", "general"]),
    })),
})
EXTRACT_SYSTEM = (
    "You extract checkable claims from marketing content for a claims-verification system. A claim is any statement "
    "a customer could hold the brand to: product properties, ingredients, environmental or health benefits, rankings and "
    "superlatives, prices, offers, numbers and statistics. Ignore greetings, opinions about feelings, and calls to action. "
    "Copy each span exactly from the content. Classify: environmental (green/natural/recyclable...), health (cures, heals, "
    "treats, safety, dermatological), superlative (#1, best, first, leading), price (prices, discounts, offers), statistic "
    "(counts, percentages, durations, study results), general (other factual properties). One claim per property."
)


def extract_llm(ctx: RunContext) -> list[Claim]:
    if not ctx.use_llm:
        return []
    res = gateway.call(gateway.Ctx("claims_extract", **ctx.llm_ctx), EXTRACT_SYSTEM,
                       json.dumps({"known_products": ctx.products(), "content": ctx.text}, ensure_ascii=False), EXTRACT_SCHEMA)
    if not res:
        return []
    text = ctx.text
    sents = sentences(text)
    health = ctx.claim_rule("health")
    out: list[Claim] = []
    used: list[tuple[int, int]] = []
    for it in res.get("claims", [])[:30]:
        span = str(it.get("span", ""))
        if not span.strip():
            continue
        idx = -1
        start = 0
        while True:
            idx = text.find(span, start)
            if idx < 0 or all(idx >= b or idx + len(span) <= a for a, b in used):
                break
            start = idx + 1
        if idx < 0:
            continue
        a, b = idx, idx + len(span)
        used.append((a, b))
        cls = str(it.get("claim_class", "general"))
        if cls not in CLASS_PRIORITY:
            cls = "general"
        s = containing(sents, a, b) or Span(a, b, span)
        qs = [Quantity(q.value, q.kind, q.unit, q.start + a, q.end + a, q.raw) for q in quantities(span)]
        banned = [h.text for h in find_phrases(span, health["params"].get("triggers", []), False)] if health else []
        if banned:
            cls = "health"
        subs = _subjects_in(ctx, s.text)
        subject = subs[0][1] if subs else _brand(ctx)
        hyp = ctx.canonicalize(str(it.get("claim") or span))
        out.append(Claim(a, b, span, cls, hyp, subject, s, qs, "llm", banned, span))
    return out


def extract(ctx: RunContext) -> list[Claim]:
    heur = extract_heuristic(ctx)
    llm = extract_llm(ctx)
    if not llm:
        return heur
    merged = list(llm)
    for h in heur:
        ov = [c for c in llm if c.start < h.end and h.start < c.end]
        if not ov:
            merged.append(h)
            continue
        # deterministic floor: banned triggers and numbers found by rules always survive
        for c in ov:
            if h.banned_hits and not c.banned_hits:
                c.banned_hits = h.banned_hits
                c.claim_class = "health"
            if h.quantities and not c.quantities:
                c.quantities = h.quantities
            if h.claim_class in NUMERIC_CLASSES and c.claim_class not in NUMERIC_CLASSES | {"environmental", "health", "superlative"}:
                c.claim_class = h.claim_class
    merged.sort(key=lambda c: c.start)
    return merged


# ------------------------------------------------------------------ verification

_fact_emb_cache: dict[str, np.ndarray] = {}


def _fact_embs(facts: list[FactView]) -> np.ndarray:
    missing = [f.statement for f in facts if f.statement not in _fact_emb_cache]
    if missing:
        for s, v in zip(missing, ml.embed(missing)):
            _fact_emb_cache[s] = v
    return np.stack([_fact_emb_cache[f.statement] for f in facts]) if facts else np.zeros((0, 1))


def _stems(s: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{3,}", s.lower())} - {"the", "and", "with", "from", "more", "than", "over"}


def _qty_context(text: str, end: int) -> set[str]:
    return _stems(" ".join(text[end:end + 40].split()[:3]))


def _equal(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005 * max(abs(a), abs(b), 1)


def _subject_adj(claim: Claim, f: FactView, ctx: RunContext) -> float:
    brand = _brand(ctx)
    if not f.subject or f.subject == brand:
        return 0.0
    if claim.subject == f.subject:
        return 0.12
    if claim.subject != brand:
        return -0.3  # a different product's fact
    return -0.05


def verify(ctx: RunContext, claims: list[Claim]) -> list[Claim]:
    facts = [f for f in ctx.facts if f.status in {"active", "expired"}]
    if not claims or not facts:
        return claims
    embs = _fact_embs(facts)
    hyp_embs = ml.embed([c.hypothesis for c in claims])
    sims = hyp_embs @ embs.T
    ambiguous: list[tuple[Claim, list[FactView]]] = []

    for i, c in enumerate(claims):
        scored = []
        for j, f in enumerate(facts):
            sc = float(sims[i, j]) + _subject_adj(c, f, ctx)
            scored.append((sc, f))
        scored.sort(key=lambda t: t[0], reverse=True)

        if c.banned_hits and c.claim_class == "health":
            c.verdict, c.method = "banned", "rule"
            c.rationale = f"Contains banned medical-claim wording: {', '.join(sorted(set(c.banned_hits)))}."
            continue

        if c.quantities and _verify_numeric(ctx, c, scored):
            continue

        cands = [(sc, f) for sc, f in scored[:3] if sc >= 0.3]
        if not cands:
            c.verdict, c.method, c.score = "unsupported", "retrieval", (scored[0][0] if scored else 0.0)
            c.rationale = "No approved fact in the ledger is close to this claim."
            continue
        probs = ml.nli([(f.statement, c.hypothesis) for _, f in cands])
        best_e = max(range(len(cands)), key=lambda k: probs[k].get("entailment", 0))
        best_c = max(range(len(cands)), key=lambda k: probs[k].get("contradiction", 0))
        pe, pc = probs[best_e], probs[best_c]
        if pe.get("entailment", 0) >= 0.6:
            f = cands[best_e][1]
            c.nli, c.score, c.method = pe, cands[best_e][0], "nli"
            _set_supported(c, f, ctx)
            continue
        if pc.get("contradiction", 0) >= 0.8 and cands[best_c][0] >= 0.5:
            f = cands[best_c][1]
            c.nli, c.score, c.method, c.verdict, c.fact_key = pc, cands[best_c][0], "nli", "contradicted", f.key
            c.rationale = f"Ledger fact {f.key} contradicts this claim (NLI {pc['contradiction']:.2f})."
            continue
        c.nli, c.score = pe, cands[0][0]
        if ctx.use_llm and cands[0][0] >= 0.4:
            ambiguous.append((c, [f for _, f in cands]))
        else:
            c.verdict, c.method = "unsupported", "nli"
            c.rationale = "No approved fact entails this claim."

    if ambiguous:
        _judge(ctx, ambiguous)
    return claims


def _set_supported(c: Claim, f: FactView, ctx: RunContext) -> None:
    if f.status == "expired":
        c.verdict, c.fact_key = "expired", f.key
        rep = ctx.fact(f.superseded_by) if f.superseded_by else None
        c.replacement_fact = rep.key if rep and rep.status == "active" else None
        c.rationale = f"Matches {f.key}, which expired on {f.valid_until}." + (f" Superseded by {rep.key}." if c.replacement_fact else "")
    else:
        c.verdict, c.fact_key = "supported", f.key
        c.rationale = f"Supported by {f.key}."


def _verify_numeric(ctx: RunContext, c: Claim, scored: list[tuple[float, FactView]]) -> bool:
    """Exact comparison for prices, percentages and counts. Returns True if decided."""
    text = ctx.text
    clause_stems = _stems(c.clause or c.text) - _stems(" ".join(ctx.products()))
    for q in c.quantities:
        ctxw = _qty_context(text, q.end)
        compat = []
        for sc, f in scored:
            if f.value is None or not units_compatible(q, f.unit):
                continue
            if c.claim_class == "price" and f.claim_class != "price":
                continue
            if c.claim_class != "price" and f.claim_class == "price" and q.kind != "inr":
                continue
            fm = re.search(r"\d[\d,]*(?:\.\d+)?", f.statement)
            fctx = _qty_context(f.statement, fm.end()) if fm else set()
            bonus = 0.15 * len(ctxw & (fctx | _stems(f.statement))) if ctxw else 0.0
            bonus += min(0.3, 0.06 * len(clause_stems & _stems(f.statement)))
            compat.append((sc + bonus, f))
        if not compat:
            continue
        compat.sort(key=lambda t: t[0], reverse=True)
        top_sc, top = compat[0]
        if top_sc < 0.3:
            continue
        equal = [(sc, f) for sc, f in compat if _equal(q.value, f.value or 0) and sc >= top_sc - 0.25]
        if equal:
            sc, f = max(equal, key=lambda t: (t[1].status == "active", t[0]))
            c.score, c.method = sc, "numeric"
            _set_supported(c, f, ctx)
            if c.verdict == "expired" and not c.replacement_fact:
                # find an active fact for the same subject and unit
                alt = next((g for _, g in compat if g.status == "active" and g.subject == f.subject), None)
                c.replacement_fact = alt.key if alt else None
            if c.verdict == "supported":
                c.rationale = f"{q.raw} matches {f.key} exactly."
            c.conflict_q = q if c.verdict == "expired" else None
            return True
        if top.status == "active" and (c.claim_class in NUMERIC_CLASSES or top_sc >= 0.45):
            c.verdict, c.fact_key, c.score, c.method, c.conflict_q = "contradicted", top.key, top_sc, "numeric", q
            c.rationale = f"Claim says {q.raw}; ledger fact {top.key} says {_fmt_value(top, q)}."
            return True
    return False


def _fmt_value(f: FactView, q: Quantity) -> str:
    v = f.value or 0
    if q.kind == "inr":
        return format_inr(v)
    if q.kind == "pct":
        return f"{v:g}%"
    raw = q.raw.lower()
    if "crore" in raw or raw.endswith("cr"):
        return f"{v / 1e7:g} crore"
    if "lakh" in raw or "lac" in raw:
        return f"{v / 1e5:g} lakh"
    if raw.rstrip("+").endswith("k"):
        return f"{v / 1e3:g}k"
    if "," in q.raw:
        return f"{int(v):,}"
    return f"{v:g}"


JUDGE_SCHEMA = OBJ({
    "judgments": ARR(OBJ({
        "id": S("integer"),
        "verdict": S("string", enum=["supported", "contradicted", "unsupported"]),
        "fact_key": S("string", description="key of the ledger fact that supports or contradicts the claim; empty if unsupported"),
        "rationale": S("string"),
    })),
})
JUDGE_SYSTEM = (
    "You verify marketing claims strictly against an approved Facts Ledger. A claim is 'supported' only if a listed fact "
    "states it (same meaning, same numbers, no extra promises). 'contradicted' if a listed fact makes it false. Otherwise "
    "'unsupported'. Vague or broader claims than the fact (e.g. 'eco-friendly' vs 'recyclable bottle', '#1' vs 'tested') "
    "are unsupported. Cite only fact keys from the candidates given for that claim."
)


def _judge(ctx: RunContext, items: list[tuple[Claim, list[FactView]]]) -> None:
    payload = [{"id": i, "claim": c.hypothesis, "candidate_facts": [{"fact_key": f.key, "fact": f.statement} for f in fs]}
               for i, (c, fs) in enumerate(items)]
    res = gateway.call(gateway.Ctx("claim_judge", **ctx.llm_ctx), JUDGE_SYSTEM, json.dumps({"claims": payload}, ensure_ascii=False), JUDGE_SCHEMA)
    by_id = {int(j.get("id", -1)): j for j in (res or {}).get("judgments", []) if str(j.get("id", "")).lstrip("-").isdigit()}
    for i, (c, fs) in enumerate(items):
        j = by_id.get(i)
        keys = {f.key: f for f in fs}
        if j and j.get("verdict") in {"supported", "contradicted"} and j.get("fact_key") in keys:
            f = keys[j["fact_key"]]
            c.method, c.rationale = "llm", str(j.get("rationale", ""))[:300]
            if j["verdict"] == "supported":
                _set_supported(c, f, ctx)
            else:
                c.verdict, c.fact_key = "contradicted", f.key
        else:
            c.verdict, c.method = "unsupported", ("llm" if j else "nli")
            c.rationale = str((j or {}).get("rationale", "")) [:300] or "No approved fact entails this claim."


# ------------------------------------------------------------------ findings


def to_findings(ctx: RunContext, claims: list[Claim]) -> list[Draft]:
    out: list[Draft] = []
    base = ctx.rule("C-0") or next(iter(ctx.rules_of("claim")), None)
    used: set[str] = set()
    for c in claims:
        rule = ctx.claim_rule(c.claim_class) or base
        if rule is None:
            continue
        f = ctx.fact(c.fact_key)
        if c.verdict == "supported":
            continue
        if c.verdict == "banned":
            out.append(Draft(check="claims", severity="block", rule_key=rule["key"], title="Medical claim",
                             message=c.rationale + " Describe how the product feels or what it contains instead.",
                             start=c.start, end=c.end, verdict="banned", source="rule", fix=None,
                             extra={"claim_class": c.claim_class, "method": c.method}))
            continue
        if c.verdict == "contradicted" and f:
            fix = None
            mode = "phrase"
            if c.conflict_q is not None and c.claim_class in NUMERIC_CLASSES:
                mode = "number"
                a, b = c.conflict_q.start, c.conflict_q.end
                fix = _fmt_value(f, c.conflict_q)
            else:
                a, b = c.start, c.end
                fix = f.approved_phrasing or None
                used.add(f.key)
            out.append(Draft(check="claims", severity="block", rule_key=rule["key"], fact_key=f.key,
                             title="Claim conflicts with an approved fact", message=c.rationale,
                             start=a, end=b, verdict="contradicted", source=c.method if c.method in {"nli", "llm"} else "rule",
                             fix=fix, confidence=_conf(c), extra={"claim_class": c.claim_class, "method": c.method, "fix_mode": mode,
                                                                  "fact_statement": f.statement, "nli": c.nli}))
            continue
        if c.verdict == "expired" and f:
            rep = ctx.fact(c.replacement_fact)
            prule = ctx.claim_rule("price") if c.claim_class == "price" else rule
            if c.conflict_q is not None and rep and rep.value is not None:
                a, b = c.conflict_q.start, c.conflict_q.end
                fix = _fmt_value(rep, c.conflict_q)
            else:
                a, b = c.start, c.end
                fix = rep.approved_phrasing if rep else None
            msg = f"{f.key} expired on {f.valid_until}." + (f" Current approved fact is {rep.key}: “{rep.statement}”" if rep else " No current replacement in the ledger.")
            out.append(Draft(check="claims", severity="block", rule_key=(prule or rule)["key"], fact_key=f.key,
                             title="Outdated information", message=msg, start=a, end=b, verdict="expired", source="rule", fix=fix,
                             extra={"claim_class": c.claim_class, "method": c.method, "replacement_fact": rep.key if rep else None,
                                    "fact_statement": f.statement}))
            continue
        # unsupported
        sev = rule["params"].get("unsupported_severity", rule["severity"])
        fix, note_fact = _unsupported_fix(ctx, c, used)
        if note_fact:
            used.add(note_fact)
        what = {"environmental": "Vague environmental claim", "superlative": "Superlative with no evidence in the ledger",
                "price": "Price or offer not in the ledger", "statistic": "Statistic not in the ledger",
                "health": "Health claim not in the ledger"}.get(c.claim_class, "Claim not in the ledger")
        out.append(Draft(check="claims", severity=sev, rule_key=rule["key"], title=what,
                         message=(c.rationale + " Remove it, or attach evidence so the claims team can add it to the ledger.").strip(),
                         start=c.start, end=c.end, verdict="unsupported", source=c.method if c.method in {"nli", "llm"} else "rule",
                         fix=fix, confidence=_conf(c), extra={"claim_class": c.claim_class, "method": c.method, "suggested_fact": note_fact}))
    return out


def _conf(c: Claim) -> float:
    if c.method == "numeric" or c.method == "rule":
        return 1.0
    if c.nli:
        return round(max(c.nli.values()), 2)
    return 0.7


def _unsupported_fix(ctx: RunContext, c: Claim, used: set[str]) -> tuple[Optional[str], Optional[str]]:
    if c.claim_class == "environmental":
        present = ctx.text.lower()
        for f in ctx.facts:
            if f.status == "active" and f.claim_class == "environmental" and f.subject in (c.subject, "") and f.approved_phrasing \
                    and f.key not in used and f.approved_phrasing.lower() not in present:
                return f.approved_phrasing, f.key
        return "", None
    if c.claim_class == "superlative":
        return "", None
    return None, None


def summarize(claims: list[Claim]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in claims:
        out[c.verdict] = out.get(c.verdict, 0) + 1
    return out
