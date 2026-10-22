from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import ROLE_LABEL, can
from .models import AICall, Comment, ContentPR, CPRVersion, Decision, Fact, Finding, Review, Rule, User
from .services import latest, required_roles


def user(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name, "role": u.role, "role_label": ROLE_LABEL.get(u.role, u.role), "title": u.title,
            "author_type": u.author_type, "team": u.team}


def iso(d) -> str | None:
    return d.isoformat() + "Z" if d is not None else None


def finding(f: Finding, decisions: list[Decision], users: dict[str, User]) -> dict[str, Any]:
    return {
        "id": f.id, "check": f.check, "severity": f.severity, "status": f.status, "rule_key": f.rule_key, "fact_key": f.fact_key,
        "policy_version": f.policy_version, "citation": f.citation, "title": f.title, "message": f.message, "start": f.start, "end": f.end,
        "excerpt": f.excerpt, "fix": f.fix_replacement, "fix_verified": f.fix_verified, "fix_note": f.fix_note, "confidence": f.confidence,
        "source": f.source, "verdict": f.verdict, "extra": f.extra or {}, "carried_from": f.carried_from,
        "decisions": [{"action": d.action, "by": users[d.actor_id].name if d.actor_id in users else d.actor_id, "role": d.role,
                       "reason": d.reason, "at": iso(d.created_at)} for d in decisions if d.finding_id in {f.id, f.carried_from}],
    }


def version_brief(v: CPRVersion) -> dict[str, Any]:
    return {"id": v.id, "number": v.number, "source": v.source, "filename": v.filename, "created_by": v.created_by, "created_at": iso(v.created_at),
            "run_status": v.run_status, "summary": v.run_summary or {}, "policy_version": v.policy_version, "sha256": v.sha256,
            "chars": len(v.text)}


def version_full(db: Session, v: CPRVersion, users: dict[str, User]) -> dict[str, Any]:
    decisions = db.scalars(select(Decision).where(Decision.cpr_id == v.cpr_id)).all()
    return {**version_brief(v), "text": v.text, "progress": v.run_progress or {}, "error": v.run_error, "claims": v.claims or [],
            "ledger_hash": v.ledger_hash, "ran_at": iso(v.ran_at), "run_ms": v.run_ms,
            "findings": [finding(f, decisions, users) for f in v.findings]}


def cpr_brief(c: ContentPR, users: dict[str, User]) -> dict[str, Any]:
    v = latest(c)
    s = v.run_summary or {}
    return {"key": c.key, "title": c.title, "channel": c.channel, "author_type": c.author_type, "status": c.status,
            "author": users[c.author_id].name if c.author_id in users else c.author_id, "author_id": c.author_id,
            "confidential": c.confidential, "versions": len(c.versions), "run_status": v.run_status, "counts": s.get("counts", {}),
            "requires_claims_review": c.requires_claims_review, "updated_at": iso(c.updated_at), "created_at": iso(c.created_at)}


def cpr_detail(db: Session, c: ContentPR, me: User) -> dict[str, Any]:
    users = {u.id: u for u in db.scalars(select(User))}
    v = latest(c)
    reviews = db.scalars(select(Review).where(Review.cpr_id == c.id).order_by(Review.created_at)).all()
    comments = db.scalars(select(Comment).where(Comment.cpr_id == c.id).order_by(Comment.created_at)).all()
    ai = db.scalars(select(AICall).where(AICall.cpr_id == c.id).order_by(AICall.created_at)).all()
    approvals_latest = {r.role for r in reviews if r.version_id == v.id and r.action == "approve"}
    is_author = c.author_id == me.id or me.role in {"brand_manager", "admin"}
    editable = c.status != "approved"
    return {
        **cpr_brief(c, users),
        "intent": c.intent or {},
        "latest": version_full(db, v, users),
        "versions": [version_brief(x) for x in sorted(c.versions, key=lambda x: x.number)],
        "reviews": [{"by": users[r.reviewer_id].name if r.reviewer_id in users else r.reviewer_id, "role": r.role, "action": r.action,
                     "note": r.note, "version": next((x.number for x in c.versions if x.id == r.version_id), None), "at": iso(r.created_at)} for r in reviews],
        "timeline": [{"kind": m.kind, "by": users[m.author_id].name if m.author_id in users else m.author_id,
                      "role": users[m.author_id].role if m.author_id in users else "", "body": m.body, "finding_id": m.finding_id,
                      "at": iso(m.created_at)} for m in comments],
        "ai_calls": [ai_call(a) for a in ai],
        "required_roles": required_roles(c),
        "approved_roles": sorted(approvals_latest),
        "receipt": c.receipt,
        "permissions": {
            "edit": editable and is_author,
            "submit": editable and is_author and c.status in {"draft", "changes_requested"},
            "review": c.status == "in_review" and me.role in required_roles(c) and me.role not in approvals_latest,
            "resolve": editable and me.role in {"brand_manager", "claims_reviewer"},
            "dispute": editable and is_author,
        },
    }


def ai_call(a: AICall) -> dict[str, Any]:
    return {"id": a.id, "at": iso(a.created_at), "cpr_id": a.cpr_id, "version_id": a.version_id, "import_id": a.import_id, "purpose": a.purpose,
            "provider": a.provider, "model": a.model, "location": a.location, "status": a.status, "redactions": a.redactions,
            "prompt_chars": a.prompt_chars, "prompt_sha256": a.prompt_sha256, "prompt_preview": a.prompt_preview, "latency_ms": a.latency_ms,
            "note": a.note}


def rule(r: Rule) -> dict[str, Any]:
    return {"id": r.id, "key": r.key, "type": r.type, "title": r.title, "severity": r.severity, "params": r.params, "section": r.section,
            "source_quote": r.source_quote, "status": r.status, "origin": r.origin, "import_id": r.import_id, "created_at": iso(r.created_at),
            "decided_by": r.decided_by, "decided_at": iso(r.decided_at)}


def fact(f: Fact, day) -> dict[str, Any]:
    return {"id": f.id, "key": f.key, "statement": f.statement, "approved_phrasing": f.approved_phrasing, "claim_class": f.claim_class,
            "subject": f.subject, "value": f.value, "unit": f.unit, "owner": f.owner, "evidence": f.evidence,
            "valid_from": f.valid_from.isoformat() if f.valid_from else None, "valid_until": f.valid_until.isoformat() if f.valid_until else None,
            "superseded_by": f.superseded_by, "verified_at": f.verified_at.isoformat() if f.verified_at else None, "status": f.status_on(day),
            "retired": f.retired}


def permissions(me: User) -> dict[str, bool]:
    return {p: can(me, p) for p in ["cpr.create", "cpr.view_all", "review", "policy.edit", "facts.edit", "ailog.view_all", "settings.edit"]}
