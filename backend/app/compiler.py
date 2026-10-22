"""Policy Compiler: guideline prose -> typed, checkable rules.

The LLM proposes rules; a deterministic parser is the fallback. Either way every
proposed rule must quote the guideline sentence it came from ("no citation, no
rule"), and nothing becomes enforceable until a brand manager approves it and
publishes a new policy version."""
from __future__ import annotations

import difflib
import io
import json
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .engine.text import normalize_ws
from .llm import gateway
from .llm.gateway import ARR, OBJ, S
from .models import GuidelineImport, Rule, now

TYPES = ["term", "voice", "claim", "a11y", "format"]
PREFIX = {"term": "T", "voice": "V", "claim": "C", "a11y": "A", "format": "P"}
A11Y_METRICS = {"reading_grade", "all_caps", "exclamations", "link_text", "alt_text", "emoji"}
CLAIM_CLASSES = {"environmental", "health", "superlative", "price", "statistic", "general"}
CHANNELS = {"instagram", "linkedin", "x", "email", "web", "blog", "sms", "print", "other", "youtube"}


# ------------------------------------------------------------------ document parsing


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            raw = "\n".join(page.get_text("text") for page in doc)
        return _reflow(raw)
    if name.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        return "\n\n".join(p.text for p in d.paragraphs if p.text.strip()).strip()
    try:
        return data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return data.decode("latin-1").strip()


def _reflow(raw: str) -> str:
    """PDF text arrives as wrapped lines. Rebuild paragraphs: a new one starts at a
    numbered clause (e.g. '8.1 ...'), a short heading-like line, or a blank line."""
    paras: list[str] = []
    cur: list[str] = []
    for line in raw.splitlines():
        t = line.strip()
        starts = bool(re.match(r"^\d+(\.\d+)*\.?\s", t))
        if not t or starts or (len(t) < 60 and not t.endswith((".", ",")) and not cur):
            if cur:
                paras.append(" ".join(cur))
                cur = []
            if not t:
                continue
        cur.append(t)
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras).strip()


# ------------------------------------------------------------------ citation check


def quote_found(quote: str, doc: str) -> bool:
    q, d = normalize_ws(_plain(quote)), normalize_ws(_plain(doc))
    if not q or len(q) < 8:
        return False
    if q in d:
        return True
    # tolerate small quoting differences (curly quotes, a dropped word)
    n = len(q)
    best = 0.0
    step = max(1, n // 4)
    for i in range(0, max(1, len(d) - n + 1), step):
        r = difflib.SequenceMatcher(None, q, d[i:i + n + 10]).ratio()
        best = max(best, r)
        if best >= 0.9:
            return True
    return False


def _plain(s: str) -> str:
    return s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'").replace("—", "-").replace("–", "-")


# ------------------------------------------------------------------ LLM compile

SCHEMA = OBJ({
    "rules": ARR(OBJ({
        "type": S("string", enum=TYPES),
        "title": S("string", description="short imperative rule title"),
        "severity": S("string", enum=["block", "warn", "suggest"]),
        "section": S("string", description="section number from the document, e.g. 8.1; empty if none"),
        "source_quote": S("string", description="the exact sentence(s) from the document this rule comes from"),
        "mode": S("string", enum=["banned", "preferred", "none"], description="term rules only"),
        "patterns": ARR(S("string")),
        "replacement": S("string"),
        "trait": S("string"),
        "flag_phrases": ARR(S("string")),
        "good_examples": ARR(S("string")),
        "bad_examples": ARR(S("string")),
        "claim_class": S("string", enum=sorted(CLAIM_CLASSES)),
        "triggers": ARR(S("string")),
        "metric": S("string", enum=sorted(A11Y_METRICS) + ["none"]),
        "max_value": S("number"),
        "channels": ARR(S("string")),
        "required_any": ARR(S("string")),
        "author_types": ARR(S("string")),
    }, required=["type", "title", "severity", "section", "source_quote"])),
})

SYSTEM = (
    "You compile brand guidelines into machine-checkable rules for Plumb. Only extract rules that can be checked on text. "
    "Types: term (banned words or preferred spellings/names: mode, patterns, replacement), voice (tone rules with flag_phrases "
    "and good/bad examples), claim (rules about factual/environmental/health/superlative/price claims: claim_class, triggers), "
    "a11y (metric: reading_grade, all_caps, exclamations, link_text, alt_text, emoji; max_value), format (channel rules: "
    "channels, required_any tokens such as hashtags or disclosures, author_types). One rule per distinct requirement; for "
    "spelling lists produce one preferred-term rule per pair. Severity: block for legal, safety or claims risk and banned words; "
    "warn for brand-voice; suggest for style preferences. source_quote MUST be copied exactly from the document. "
    "Skip visual-design rules (colours, logos) -- they are not checkable on text."
)


def _normalize(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    t = raw.get("type")
    if t not in TYPES:
        return None
    sev = raw.get("severity") if raw.get("severity") in {"block", "warn", "suggest"} else "warn"
    lst = lambda k: [str(x).strip() for x in (raw.get(k) or []) if str(x).strip()][:30]  # noqa: E731
    p: dict[str, Any]
    if t == "term":
        pats = lst("patterns")
        if not pats:
            return None
        mode = raw.get("mode") if raw.get("mode") in {"banned", "preferred"} else ("preferred" if raw.get("replacement") else "banned")
        p = {"mode": mode, "patterns": pats, "replacement": (str(raw.get("replacement") or "").strip() or None), "case_sensitive": False}
    elif t == "voice":
        fl = lst("flag_phrases")
        if not fl and not lst("bad_examples"):
            return None
        sev = "warn" if sev == "block" else sev
        p = {"trait": str(raw.get("trait") or raw.get("title") or "")[:120], "flag_phrases": [f.lower() for f in fl], "replacements": {},
             "good": lst("good_examples"), "bad": lst("bad_examples")}
    elif t == "claim":
        cc = raw.get("claim_class") if raw.get("claim_class") in CLAIM_CLASSES else "general"
        p = {"claim_class": cc, "requirement": "banned" if cc == "health" else ("needs_fact" if cc in {"environmental", "superlative"} else "match_ledger"),
             "unsupported_severity": sev, "triggers": [x.lower() for x in lst("triggers")]}
    elif t == "a11y":
        m = raw.get("metric")
        if m not in A11Y_METRICS:
            return None
        p = {"metric": m}
        if raw.get("max_value") is not None:
            p["max"] = float(raw["max_value"])
    else:
        req = lst("required_any")
        if not req:
            return None
        chans = [c.lower() for c in lst("channels") if c.lower() in CHANNELS]
        p = {"kind": "required_any", "required_any": req, "channels": chans, "author_types": [a.lower() for a in lst("author_types")],
             "suggestion": req[0]}
    sec = str(raw.get("section") or "").strip().lstrip("§")
    return {"type": t, "title": str(raw.get("title") or "")[:200] or t, "severity": sev, "section": f"§{sec}" if sec else "",
            "source_quote": str(raw.get("source_quote") or "").strip(), "params": p}


# ------------------------------------------------------------------ deterministic fallback parser

_PAIR = re.compile(r"[\"“]([^\"”]+)[\"”]\s*\(?\s*(?:not|instead of|rather than)\s+[\"“]([^\"”]+)[\"”]\)?", re.I)
_QUOTED = re.compile(r"[\"“]([^\"”]{2,40})[\"”]")
_NEG = re.compile(r"\b(never|do not|don't|avoid|not permitted|not allowed|must not|no longer)\b", re.I)
_TONE = re.compile(r"\b(tone|voice|hype|sound|sounds|overused|calm|pushy|jargon)\b", re.I)
_HASH = re.compile(r"#\w+")
_SEC = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+")


def parse_fallback(doc: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for para in re.split(r"\n\s*\n", doc):
        para = para.strip()
        if not para or para.startswith("#") or para.startswith(">"):
            continue
        m = _SEC.match(para)
        section = f"§{m.group(1)}" if m else ""
        body = para[m.end():] if m else para
        for sent in re.split(r"(?<=[.!?])\s+", body):
            pairs = _PAIR.findall(sent)
            if pairs:
                for good, bad in pairs:
                    out.append({"type": "term", "title": f"Write “{good}”, not “{bad}”", "severity": "suggest", "section": section,
                                "source_quote": sent.strip(), "params": {"mode": "preferred", "patterns": [bad], "replacement": good, "case_sensitive": False}})
                continue
            low = sent.lower()
            if re.search(r"\b(must|always)\s+(include|carry|use|contain)\b", low) and _HASH.search(sent):
                tags = _HASH.findall(sent)
                chans = [c for c in CHANNELS if re.search(rf"\b{c}\b", low)]
                out.append({"type": "format", "title": f"Include {' / '.join(tags)}", "severity": "warn", "section": section,
                            "source_quote": sent.strip(), "params": {"kind": "required_any", "required_any": tags, "channels": chans,
                                                                    "author_types": [], "suggestion": tags[0]}})
                continue
            quoted = [q for q in _QUOTED.findall(sent) if not q.startswith("#")]
            if quoted and _NEG.search(sent):
                if _TONE.search(sent) or re.search(r"\bwords?\b", low):
                    out.append({"type": "voice", "title": re.sub(r"\s+", " ", sent.strip())[:90], "severity": "warn", "section": section,
                                "source_quote": sent.strip(), "params": {"trait": sent.strip()[:120], "flag_phrases": [q.lower() for q in quoted],
                                                                        "replacements": {}, "good": [], "bad": []}})
                else:
                    out.append({"type": "term", "title": f"Don't use {', '.join('“' + q + '”' for q in quoted[:3])}", "severity": "block"
                                if re.search(r"competitor|legal|never", low) else "warn", "section": section, "source_quote": sent.strip(),
                                "params": {"mode": "banned", "patterns": quoted, "replacement": None, "case_sensitive": False}})
    return out


# ------------------------------------------------------------------ orchestration


def _dupe(db: Session, rule: dict[str, Any]) -> bool:
    p = rule["params"]
    for r in db.scalars(select(Rule).where(Rule.type == rule["type"], Rule.status.in_(["approved", "proposed"]))):
        rp = r.params or {}
        if rule["type"] == "term" and {x.lower() for x in p.get("patterns", [])} <= {x.lower() for x in rp.get("patterns", [])}:
            return True
        if rule["type"] == "voice" and p.get("flag_phrases") and set(p["flag_phrases"]) <= {x.lower() for x in rp.get("flag_phrases", [])}:
            return True
        if rule["type"] == "format" and set(p.get("required_any", [])) == set(rp.get("required_any", [])):
            return True
        if rule["type"] == "a11y" and p.get("metric") == rp.get("metric"):
            return True
        if rule["type"] == "claim" and p.get("claim_class") == rp.get("claim_class"):
            return True
    return False


def next_key(db: Session, type_: str) -> str:
    pre = PREFIX[type_]
    nums = [int(k.split("-")[1]) for k in db.scalars(select(Rule.key).where(Rule.key.like(f"{pre}-%"))) if k.split("-")[1].isdigit()]
    return f"{pre}-{(max(nums) + 1) if nums else 1}"


def compile_import(import_id: int) -> None:
    db = SessionLocal()
    try:
        imp = db.get(GuidelineImport, import_id)
        if imp is None:
            return
        imp.status = "running"
        db.commit()
        res = gateway.call(gateway.Ctx("compile", import_id=imp.id), SYSTEM, json.dumps({"document": imp.text[:30000]}, ensure_ascii=False), SCHEMA)
        method = "llm"
        if res is not None:
            proposed = [r for r in (_normalize(x) for x in res.get("rules", [])) if r]
        else:
            method = "parser"
            proposed = parse_fallback(imp.text)
        dropped = dup = added = 0
        for r in proposed:
            if not quote_found(r["source_quote"], imp.text):
                dropped += 1
                continue
            if _dupe(db, r):
                dup += 1
                continue
            db.add(Rule(key=next_key(db, r["type"]), type=r["type"], title=r["title"], severity=r["severity"], params=r["params"],
                        section=r["section"], source_quote=r["source_quote"], status="proposed", origin="compiled", import_id=imp.id))
            db.flush()
            added += 1
        imp.proposed, imp.dropped_uncited, imp.duplicates = added, dropped, dup
        imp.status = "done"
        imp.error = "" if method == "llm" else "LLM unavailable — used Plumb's deterministic guideline parser (handles quoted terms, “X not Y” spellings and required hashtags)."
        db.commit()
    except Exception as e:
        db.rollback()
        imp = db.get(GuidelineImport, import_id)
        if imp is not None:
            imp.status, imp.error = "error", f"{type(e).__name__}: {e}"
            db.commit()
    finally:
        db.close()


def publish(db: Session, user_id: str, note: str):
    from .models import PolicyVersion

    approved = db.scalars(select(Rule).where(Rule.status == "approved").order_by(Rule.type, Rule.key)).all()
    snapshot = [{"key": r.key, "type": r.type, "title": r.title, "severity": r.severity, "params": r.params, "section": r.section,
                 "source_quote": r.source_quote} for r in approved]
    cur = db.scalar(select(PolicyVersion).where(PolicyVersion.is_current.is_(True)))
    if cur is not None:
        if json_eq(cur.rules, snapshot):
            from fastapi import HTTPException

            raise HTTPException(409, "No changes since the current policy version.")
        cur.is_current = False
    n = (cur.number + 1) if cur else 1
    pv = PolicyVersion(number=n, note=note.strip()[:500] or f"Policy v{n}", rules=snapshot, published_by=user_id, published_at=now(), is_current=True)
    db.add(pv)
    db.commit()
    return pv


def json_eq(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def diff(prev: list[dict[str, Any]], cur: list[dict[str, Any]]) -> dict[str, list[str]]:
    a = {r["key"]: r for r in prev}
    b = {r["key"]: r for r in cur}
    return {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "changed": sorted(k for k in set(a) & set(b) if not json_eq(a[k], b[k]))}
