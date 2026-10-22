"""Demo authentication + role-based access control.

Plumb's demo workspace has no passwords (judges must be able to try every role
without signing in). The active user is sent in the X-Plumb-User header, and
every permission is enforced server-side from that user's role."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import ContentPR, User

PERMS: dict[str, set[str]] = {
    "cpr.create": {"creator", "brand_manager", "admin"},
    "cpr.view_all": {"brand_manager", "claims_reviewer", "admin"},
    "review": {"brand_manager", "claims_reviewer"},
    "policy.edit": {"brand_manager", "admin"},
    "facts.edit": {"claims_reviewer", "admin"},
    "ailog.view_all": {"admin", "brand_manager"},
    "settings.edit": {"admin"},
}

ROLE_LABEL = {"creator": "Creator", "brand_manager": "Brand manager", "claims_reviewer": "Claims reviewer", "admin": "Admin"}


def current_user(x_plumb_user: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not x_plumb_user:
        raise HTTPException(401, "Choose a demo user (X-Plumb-User header missing).")
    u = db.get(User, x_plumb_user)
    if u is None:
        raise HTTPException(401, f"Unknown user '{x_plumb_user}'.")
    return u


def can(user: User, perm: str) -> bool:
    return user.role in PERMS.get(perm, set())


def require(user: User, perm: str) -> None:
    if not can(user, perm):
        raise HTTPException(403, f"{ROLE_LABEL.get(user.role, user.role)} can't do that ({perm}).")


def can_view_cpr(user: User, cpr: ContentPR) -> bool:
    return can(user, "cpr.view_all") or cpr.author_id == user.id


def require_view(user: User, cpr: ContentPR) -> None:
    if not can_view_cpr(user, cpr):
        raise HTTPException(404, "Content PR not found.")


def require_author(user: User, cpr: ContentPR) -> None:
    if cpr.author_id != user.id and user.role not in {"brand_manager", "admin"}:
        raise HTTPException(403, "Only the author can change this draft.")
