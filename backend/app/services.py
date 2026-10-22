"""Content Pull Request workflow: the state machine that sits on top of the
check engine. Pure functions over a DB session -- the API layer only adds HTTP."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import receipts
from .auth import ROLE_LABEL
from .engine.runner import current_policy
from .engine.text import apply_edits
from .models import Comment, ContentPR, CPRVersion, Decision, Finding, Review, User, now

CHANNELS = ["instagram", "linkedin", "x", "email", "web", "blog", "sms", "print", "other"]
AUTHOR_TYPES = ["human", "agency", "ai", "influencer"]
OPEN_BLOCKING = {"open", "disputed", "upheld"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def latest(cpr: ContentPR) -> CPRVersion:
    return max(cpr.versions, key=lambda v: v.number)


def event(db: Session, cpr: ContentPR, user: User, body: str, version: Optional[CPRVersion] = None) -> None:
    db.add(Comment(cpr_id=cpr.id, version_id=version.id if version else None, author_id=user.id, body=body, kind="event"))


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def create_cpr(db: Session, user: User, *, title: str, channel: str, author_type: str, text: str, source: str = "paste",
               filename: str = "", confidential: bool = False) -> ContentPR:
    text = _clean(text)
    if not text:
        raise HTTPException(400, "The draft is empty.")
    if len(text) > 20000:
        raise HTTPException(400, "Drafts are limited to 20,000 characters in this demo.")
    if channel not in CHANNELS:
        raise HTTPException(400, f"Unknown channel '{channel}'.")
    if author_type not in AUTHOR_TYPES:
        raise HTTPException(400, f"Unknown author type '{author_type}'.")
    n = (db.scalar(select(func.max(ContentPR.id))) or 0) + 1
    cpr = ContentPR(key=f"CPR-{140 + n:04d}", title=(title.strip() or "Untitled draft")[:200], channel=channel, author_type=author_type,
                    author_id=user.id, confidential=confidential, status="draft", intent={})
    db.add(cpr)
    db.flush()
    v = CPRVersion(cpr_id=cpr.id, number=1, text=text, sha256=sha(text), source=source, filename=filename, created_by=user.id)
    db.add(v)
    db.flush()
    event(db, cpr, user, f"opened {cpr.key} ({channel}, {author_type}{', confidential' if confidential else ''})", v)
    db.commit()
    db.refresh(cpr)
    return cpr


def _assert_editable(cpr: ContentPR) -> None:
    if cpr.status == "approved":
        raise HTTPException(409, "Approved content is locked. Open a new Content PR to change it.")


def new_version(db: Session, cpr: ContentPR, user: User, text: str, source: str = "edit") -> CPRVersion:
    _assert_editable(cpr)
    text = _clean(text)
    if not text:
        raise HTTPException(400, "The draft is empty.")
    cur = latest(cpr)
    if text == cur.text:
        raise HTTPException(409, "No changes to save.")
    v = CPRVersion(cpr_id=cpr.id, number=cur.number + 1, text=text, sha256=sha(text), source=source, created_by=user.id)
    db.add(v)
    db.flush()
    if cpr.status in {"in_review", "changes_requested"}:
        cpr.status = "draft"
        event(db, cpr, user, f"edited the draft (v{v.number}); review restarts after re-submission", v)
    else:
        event(db, cpr, user, f"saved v{v.number} ({'accepted fixes' if source == 'fix' else 'manual edit'})", v)
    db.commit()
    db.refresh(cpr)
    return v


def update_intent(db: Session, cpr: ContentPR, user: User, data: dict[str, Any]) -> None:
    _assert_editable(cpr)
    keep = [k.strip() for k in data.get("must_keep", []) if isinstance(k, str) and k.strip()][:6]
    cpr.intent = {"goal": str(data.get("goal", ""))[:160], "audience": str(data.get("audience", ""))[:160],
                  "cta": str(data.get("cta", ""))[:160], "key_message": str(data.get("key_message", ""))[:240],
                  "must_keep": keep, "source": (cpr.intent or {}).get("source", "manual"), "confirmed": True}
    event(db, cpr, user, "confirmed the intent (goal, audience, call to action)")
    db.commit()


def _finding(db: Session, cpr: ContentPR, finding_id: int) -> Finding:
    f = db.get(Finding, finding_id)
    if f is None or f.cpr_id != cpr.id:
        raise HTTPException(404, "Finding not found.")
    return f


def apply_fixes(db: Session, cpr: ContentPR, user: User, items: list[dict[str, Any]]) -> CPRVersion:
    _assert_editable(cpr)
    cur = latest(cpr)
    if cur.run_status != "done":
        raise HTTPException(409, "Wait for the checks to finish.")
    edits, accepted = [], []
    for it in items:
        f = _finding(db, cpr, int(it["finding_id"]))
        if f.version_id != cur.id:
            raise HTTPException(409, "That finding belongs to an older version.")
        if f.status != "open":
            continue
        rep = it.get("replacement")
        edited = rep is not None and rep != f.fix_replacement
        rep = f.fix_replacement if rep is None else str(rep)
        if rep is None:
            raise HTTPException(400, f"Finding {f.id} has no suggested fix; edit the text instead.")
        edits.append((f.start, f.end, rep))
        accepted.append((f, edited, rep))
    if not edits:
        raise HTTPException(400, "Nothing to apply.")
    new_text, skipped = apply_edits(cur.text, edits)
    skipped_set = {(a, b) for a, b, _ in skipped}
    for f, edited, rep in accepted:
        if (f.start, f.end) in skipped_set:
            continue
        f.status = "accepted"
        db.add(Decision(cpr_id=cpr.id, finding_id=f.id, rule_key=f.rule_key, fact_key=f.fact_key, actor_id=user.id, role=user.role,
                        action="edit" if edited else "accept", reason=(f"Changed suggestion to “{rep}”" if edited else "")))
    db.flush()
    return new_version(db, cpr, user, new_text, source="fix")


def dispute(db: Session, cpr: ContentPR, user: User, finding_id: int, reason: str) -> Finding:
    _assert_editable(cpr)
    f = _finding(db, cpr, finding_id)
    if f.version_id != latest(cpr).id:
        raise HTTPException(409, "That finding belongs to an older version.")
    if f.status not in {"open", "upheld"}:
        raise HTTPException(409, f"Finding is already {f.status}.")
    if len(reason.strip()) < 5:
        raise HTTPException(400, "Say why you disagree (a few words).")
    f.status = "disputed"
    db.add(Decision(cpr_id=cpr.id, finding_id=f.id, rule_key=f.rule_key, fact_key=f.fact_key, actor_id=user.id, role=user.role,
                    action="dispute", reason=reason.strip()[:600]))
    event(db, cpr, user, f"disputed {f.rule_key}{'/' + f.fact_key if f.fact_key else ''}: “{reason.strip()[:120]}”")
    db.commit()
    return f


def resolve(db: Session, cpr: ContentPR, user: User, finding_id: int, action: str, reason: str) -> Finding:
    if user.role not in {"brand_manager", "claims_reviewer"}:
        raise HTTPException(403, "Only reviewers can override or uphold findings.")
    _assert_editable(cpr)
    f = _finding(db, cpr, finding_id)
    if f.check == "claims" and user.role != "claims_reviewer" and f.severity == "block":
        raise HTTPException(403, "Blocking claim findings can only be overridden by the claims reviewer.")
    if action not in {"override", "uphold"}:
        raise HTTPException(400, "action must be override or uphold")
    if action == "override" and len(reason.strip()) < 5:
        raise HTTPException(400, "Overrides need a written reason.")
    if f.status not in {"open", "disputed"}:
        raise HTTPException(409, f"Finding is already {f.status}.")
    f.status = "overridden" if action == "override" else "upheld"
    db.add(Decision(cpr_id=cpr.id, finding_id=f.id, rule_key=f.rule_key, fact_key=f.fact_key, actor_id=user.id, role=user.role,
                    action=action, reason=reason.strip()[:600]))
    event(db, cpr, user, f"{'overrode' if action == 'override' else 'upheld'} {f.rule_key}{'/' + f.fact_key if f.fact_key else ''}"
                         + (f": “{reason.strip()[:120]}”" if reason.strip() else ""))
    db.commit()
    return f


def blockers(v: CPRVersion) -> list[Finding]:
    return [f for f in v.findings if f.severity == "block" and f.status in {"open", "upheld"}]


def required_roles(cpr: ContentPR) -> list[str]:
    return ["brand_manager"] + (["claims_reviewer"] if cpr.requires_claims_review else [])


def submit(db: Session, cpr: ContentPR, user: User) -> None:
    _assert_editable(cpr)
    if cpr.status == "in_review":
        raise HTTPException(409, "Already in review.")
    v = latest(cpr)
    if v.run_status != "done":
        raise HTTPException(409, "Run the checks on the latest version first.")
    if v.policy_version != current_policy(db).number:
        raise HTTPException(409, f"The policy changed since this version was checked (v{v.policy_version} → v{current_policy(db).number}). Re-run the checks.")
    b = blockers(v)
    if b:
        raise HTTPException(409, f"{len(b)} blocking finding(s) still open. Accept a fix, edit the text, or dispute them with a reason.")
    if not (cpr.intent or {}).get("confirmed"):
        cpr.intent = {**(cpr.intent or {}), "confirmed": True}
    cpr.status = "in_review"
    who = " and ".join(ROLE_LABEL[r].lower() for r in required_roles(cpr))
    event(db, cpr, user, f"submitted v{v.number} for review → routed to {who}", v)
    db.commit()


def review(db: Session, cpr: ContentPR, user: User, action: str, note: str) -> None:
    if user.role not in {"brand_manager", "claims_reviewer"}:
        raise HTTPException(403, "Only reviewers can approve or request changes.")
    if cpr.status != "in_review":
        raise HTTPException(409, "This Content PR is not waiting for review.")
    if user.role not in required_roles(cpr):
        raise HTTPException(403, "Your review isn't required on this Content PR.")
    v = latest(cpr)
    if action == "request_changes":
        if len(note.strip()) < 3:
            raise HTTPException(400, "Tell the creator what to change.")
        db.add(Review(cpr_id=cpr.id, version_id=v.id, reviewer_id=user.id, role=user.role, action=action, note=note.strip()[:1000]))
        cpr.status = "changes_requested"
        event(db, cpr, user, f"requested changes on v{v.number}: “{note.strip()[:160]}”", v)
        db.commit()
        return
    if action != "approve":
        raise HTTPException(400, "action must be approve or request_changes")
    pending = [f for f in v.findings if f.severity == "block" and f.status in OPEN_BLOCKING]
    if pending:
        raise HTTPException(409, f"{len(pending)} blocking finding(s) are unresolved (open, disputed or upheld). Override with a reason or request changes.")
    already = db.scalar(select(Review).where(Review.version_id == v.id, Review.role == user.role, Review.action == "approve"))
    if already:
        raise HTTPException(409, "You already approved this version.")
    db.add(Review(cpr_id=cpr.id, version_id=v.id, reviewer_id=user.id, role=user.role, action="approve", note=note.strip()[:1000]))
    db.flush()
    approved_roles = {r.role for r in db.scalars(select(Review).where(Review.version_id == v.id, Review.action == "approve"))}
    missing = [r for r in required_roles(cpr) if r not in approved_roles]
    if missing:
        event(db, cpr, user, f"approved v{v.number} · waiting for {', '.join(ROLE_LABEL[m].lower() for m in missing)}", v)
        db.commit()
        return
    cpr.status = "approved"
    cpr.approved_version_id = v.id
    event(db, cpr, user, f"approved v{v.number} · all required reviews complete · receipt issued", v)
    db.flush()
    db.refresh(cpr)
    cpr.receipt = receipts.build(db, cpr, v)
    db.commit()


def comment(db: Session, cpr: ContentPR, user: User, body: str, finding_id: Optional[int] = None) -> Comment:
    body = body.strip()
    if not body:
        raise HTTPException(400, "Empty comment.")
    c = Comment(cpr_id=cpr.id, version_id=latest(cpr).id, finding_id=finding_id, author_id=user.id, body=body[:2000], kind="comment")
    db.add(c)
    db.commit()
    return c
