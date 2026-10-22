"""Check run orchestration for one CPR version."""
from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import ContentPR, CPRVersion, Fact, Finding, PolicyVersion, now
from . import claims as claims_mod
from . import intent as intent_mod
from . import ml
from .checks import check_voice_llm, run_deterministic
from .core import CHECKS, SEVERITY_RANK, Draft, FactView, RunContext, ledger_hash, today
from .fixes import verify_all

log = logging.getLogger("plumb.runner")

STEPS = ["intent", "rules", "claims", "voice", "verify", "fixes", "save"]


def current_policy(db: Session) -> PolicyVersion:
    pv = db.scalar(select(PolicyVersion).where(PolicyVersion.is_current.is_(True)))
    if pv is None:
        raise RuntimeError("No published policy version")
    return pv


def fact_views(db: Session, day: dt.date | None = None) -> list[FactView]:
    day = day or today()
    out = []
    for f in db.scalars(select(Fact).order_by(Fact.key)):
        out.append(FactView(key=f.key, statement=f.statement, approved_phrasing=f.approved_phrasing, claim_class=f.claim_class,
                            subject=f.subject, value=f.value, unit=f.unit, owner=f.owner, evidence=f.evidence,
                            status=f.status_on(day), valid_until=f.valid_until.isoformat() if f.valid_until else None,
                            superseded_by=f.superseded_by, verified_at=f.verified_at.isoformat() if f.verified_at else None))
    return out


def build_context(db: Session, cpr: ContentPR, text: str, version_id: int | None, use_llm: bool = True) -> RunContext:
    pv = current_policy(db)
    return RunContext(text=text, channel=cpr.channel, author_type=cpr.author_type, rules=pv.rules, facts=fact_views(db),
                      policy_version=pv.number, intent=cpr.intent or {}, use_llm=use_llm and settings.llm_enabled and not cpr.confidential,
                      llm_ctx={"cpr_id": cpr.id, "version_id": version_id, "confidential": cpr.confidential})


def analyse(ctx: RunContext, progress=lambda step: None) -> tuple[list[Draft], list[claims_mod.Claim], dict[str, Any]]:
    """Pure analysis (no DB writes). Used by the runner, the eval harness and tests."""
    t0 = time.time()
    progress("rules")
    drafts = run_deterministic(ctx)
    progress("claims")
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_claims = ex.submit(claims_mod.extract, ctx)
        fut_voice = ex.submit(check_voice_llm, ctx, [d for d in drafts if d.check == "voice"])
        claims = fut_claims.result()
        progress("voice")
        voice_llm = fut_voice.result()
    progress("verify")
    claims_mod.verify(ctx, claims)
    drafts += voice_llm + claims_mod.to_findings(ctx, claims)

    # "No citation, no finding": every finding must cite a rule in this policy version
    # (and, for claims, a fact that exists in the ledger). Anything else is dropped.
    rule_keys = {r["key"] for r in ctx.rules}
    fact_keys = {f.key for f in ctx.facts}
    valid, dropped = [], 0
    seen: set[tuple] = set()
    for d in drafts:
        ok = d.rule_key in rule_keys and (d.fact_key is None or d.fact_key in fact_keys) and 0 <= d.start <= d.end <= len(ctx.text)
        if not ok:
            dropped += 1
            continue
        sig = (d.rule_key, d.fact_key, d.start, d.end)
        if sig in seen:
            continue
        seen.add(sig)
        valid.append(d)
    progress("fixes")
    verify_all(ctx, valid)
    valid.sort(key=lambda d: (d.start, -SEVERITY_RANK.get(d.severity, 0)))

    summary: dict[str, Any] = {"checks": {}, "claims": claims_mod.summarize(claims), "dropped_uncited": dropped,
                               "llm_used": ctx.use_llm, "ml": ml.status()["loaded"], "ms": int((time.time() - t0) * 1000),
                               "policy_version": ctx.policy_version}
    for c in CHECKS:
        fs = [d for d in valid if d.check == c]
        worst = max((SEVERITY_RANK[d.severity] for d in fs), default=0)
        summary["checks"][c] = {"status": {3: "block", 2: "warn", 1: "suggest", 0: "pass"}[worst], "count": len(fs)}
    summary["counts"] = {s: sum(1 for d in valid if d.severity == s) for s in ("block", "warn", "suggest")}
    return valid, claims, summary


def run_version(version_id: int, use_llm: bool = True) -> None:
    db = SessionLocal()
    try:
        v = db.get(CPRVersion, version_id)
        if v is None:
            return
        cpr = db.get(ContentPR, v.cpr_id)
        v.run_status, v.run_error, v.run_progress = "running", "", {"step": "intent", "steps": STEPS}
        db.commit()

        def progress(step: str) -> None:
            v.run_progress = {"step": step, "steps": STEPS}
            db.commit()

        ctx = build_context(db, cpr, v.text, v.id, use_llm=use_llm)
        if not ctx.use_llm:
            from ..llm import gateway

            reason = ("Confidential content: external model calls are blocked; deterministic checks and local NLI only." if cpr.confidential
                      else f"LLM unavailable ({gateway.status()['reason']}); deterministic checks and local NLI only." if not settings.llm_enabled
                      else "Offline run (seeded example); no external model calls.")
            gateway._log(gateway.Ctx("check_run", cpr_id=cpr.id, version_id=v.id), provider="none", model="", location="none",
                         status="skipped", note=reason)
        if not cpr.intent:
            cpr.intent = intent_mod.extract(ctx, cpr.title)
            ctx.intent = cpr.intent
            db.commit()
        drafts, claims, summary = analyse(ctx, progress)
        progress("save")

        prev = db.scalar(select(CPRVersion).where(CPRVersion.cpr_id == cpr.id, CPRVersion.number == v.number - 1))
        carry: dict[tuple, Finding] = {}
        if prev is not None:
            for f in prev.findings:
                if f.status in {"disputed", "upheld", "overridden"}:
                    carry[(f.rule_key, f.fact_key, f.excerpt.lower())] = f

        for f in list(v.findings):
            db.delete(f)
        db.flush()
        for d in drafts:
            excerpt = v.text[d.start:d.end]
            fnd = Finding(cpr_id=cpr.id, version_id=v.id, check=d.check, severity=d.severity, rule_key=d.rule_key, fact_key=d.fact_key,
                          policy_version=ctx.policy_version, citation=_citation(ctx, d), title=d.title[:240], message=d.message,
                          start=d.start, end=d.end, excerpt=excerpt, fix_replacement=d.fix, fix_verified=d.fix_verified,
                          fix_note=d.fix_note, confidence=d.confidence, source=d.source, verdict=d.verdict, extra=_jsonable(d.extra))
            old = carry.get((d.rule_key, d.fact_key, excerpt.lower()))
            if old is not None:
                fnd.status, fnd.carried_from = old.status, old.id
            db.add(fnd)
        v.claims = [c.to_json() for c in claims]
        v.run_summary = summary
        v.policy_version = ctx.policy_version
        v.ledger_hash = ledger_hash(ctx.facts)
        v.ran_at = now()
        v.run_ms = summary["ms"]
        v.run_status = "done"
        v.run_progress = {"step": "done", "steps": STEPS}
        cpr.requires_claims_review = bool(claims) or any(d.check == "claims" for d in drafts)
        db.commit()
    except Exception as e:  # surface the error on the version instead of failing silently
        log.exception("run failed")
        db.rollback()
        v = db.get(CPRVersion, version_id)
        if v is not None:
            v.run_status, v.run_error = "error", f"{type(e).__name__}: {e}"
            db.commit()
    finally:
        db.close()


def _citation(ctx: RunContext, d: Draft) -> str:
    r = ctx.rule(d.rule_key)
    parts = []
    if r:
        parts.append(f"{r['section']} {r['key']}: “{r['source_quote']}”")
    f = ctx.fact(d.fact_key)
    if f:
        parts.append(f"{f.key} ({f.owner}, verified {f.verified_at or 'n/a'}): “{f.statement}”")
    return "\n".join(parts)


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return round(x, 4)
    return x
