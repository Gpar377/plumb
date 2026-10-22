"""Audit receipts: a signed, self-contained record of why a piece of content was
allowed to ship -- which version, which policy, which ledger, which checks, who
decided what, and which AI calls were made."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import AICall, ContentPR, CPRVersion, Decision, Review, User, now


def _canonical(d: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in d.items() if k != "signature"}, sort_keys=True, ensure_ascii=False, default=str).encode()


def sign(d: dict[str, Any]) -> str:
    return hmac.new(settings.receipt_secret.encode(), _canonical(d), hashlib.sha256).hexdigest()


def verify(d: dict[str, Any]) -> bool:
    sig = d.get("signature")
    return isinstance(sig, str) and hmac.compare_digest(sig, sign(d))


def build(db: Session, cpr: ContentPR, v: CPRVersion) -> dict[str, Any]:
    users = {u.id: u for u in db.scalars(select(User))}
    reviews = db.scalars(select(Review).where(Review.version_id == v.id, Review.action == "approve").order_by(Review.created_at)).all()
    decisions = db.scalars(select(Decision).where(Decision.cpr_id == cpr.id).order_by(Decision.created_at)).all()
    calls = db.scalars(select(AICall).where(AICall.cpr_id == cpr.id).order_by(AICall.created_at)).all()
    r: dict[str, Any] = {
        "receipt_id": f"RCPT-{cpr.key}-v{v.number}",
        "issued_at": now().isoformat() + "Z",
        "content": {"cpr": cpr.key, "title": cpr.title, "channel": cpr.channel, "author": users[cpr.author_id].name if cpr.author_id in users else cpr.author_id,
                    "author_type": cpr.author_type, "confidential": cpr.confidential},
        "approved_version": {"number": v.number, "sha256": v.sha256, "characters": len(v.text), "versions_total": len(cpr.versions)},
        "intent": {k: cpr.intent.get(k) for k in ("goal", "audience", "cta", "must_keep", "confirmed") if k in (cpr.intent or {})},
        "policy_version": v.policy_version,
        "ledger_sha256": v.ledger_hash,
        "checks": (v.run_summary or {}).get("checks", {}),
        "claims": [{"text": c["text"], "verdict": c["verdict"], "fact": c.get("fact_key")} for c in (v.claims or [])],
        "open_findings_at_approval": [
            {"rule": f.rule_key, "fact": f.fact_key, "severity": f.severity, "status": f.status, "excerpt": f.excerpt[:120]}
            for f in v.findings if f.status in {"open", "overridden", "disputed"}
        ],
        "decisions": [{"finding": d.finding_id, "rule": d.rule_key, "fact": d.fact_key, "action": d.action,
                       "by": users[d.actor_id].name if d.actor_id in users else d.actor_id, "role": d.role, "reason": d.reason,
                       "at": d.created_at.isoformat() + "Z"} for d in decisions],
        "approvals": [{"by": users[x.reviewer_id].name if x.reviewer_id in users else x.reviewer_id, "role": x.role, "note": x.note,
                       "at": x.created_at.isoformat() + "Z"} for x in reviews],
        "ai_disclosure": {
            "external_calls": sum(1 for c in calls if c.location == "external" and c.status in {"ok", "invalid", "error"}),
            "cached_calls": sum(1 for c in calls if c.status == "cached"),
            "skipped_calls": sum(1 for c in calls if c.status == "skipped"),
            "models": sorted({c.model for c in calls if c.model}),
            "local_models": ["sentence-transformers/all-MiniLM-L6-v2 (retrieval)", "cross-encoder/nli-deberta-v3-small (entailment)"],
            "redactions": sum(c.redactions for c in calls),
            "calls": [{"purpose": c.purpose, "provider": c.provider, "model": c.model, "status": c.status, "location": c.location,
                       "at": c.created_at.isoformat() + "Z"} for c in calls],
        },
        "signature_alg": "HMAC-SHA256",
    }
    r["signature"] = sign(r)
    return r
