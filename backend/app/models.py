"""Persistence model.

Vocabulary
- Rule: one typed, checkable brand rule, linked to the guideline clause it came from.
- PolicyVersion: an immutable snapshot of approved rules. Every check run records the
  version it ran on, so old findings stay explainable after the policy changes.
- Fact: an approved claim in the Facts Ledger, with owner, evidence and a validity window.
- ContentPR (CPR): a piece of content moving towards publication.
- CPRVersion: one immutable text revision of a CPR; checks run per version.
- Finding: one issue on one version. Must cite a rule key and/or fact key.
- Decision: a human action on a finding (accept / edit / dispute / uphold / override).
- Review: an approval or change request from a reviewer on a version.
- AICall: the disclosure log -- every model call (or skipped call) is recorded here.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(30))  # creator | brand_manager | claims_reviewer | admin
    title: Mapped[str] = mapped_column(String(160), default="")
    author_type: Mapped[str] = mapped_column(String(20), default="human")  # human | agency | ai | influencer
    team: Mapped[str] = mapped_column(String(80), default="Marketing")


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(20))  # term | voice | claim | a11y | format
    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(10))  # block | warn | suggest
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    section: Mapped[str] = mapped_column(String(40), default="")
    source_quote: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="approved")  # proposed | approved | rejected | retired
    origin: Mapped[str] = mapped_column(String(12), default="seed")  # seed | compiled | manual
    import_id: Mapped[Optional[int]] = mapped_column(ForeignKey("guideline_imports.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    decided_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # snapshot
    published_by: Mapped[str] = mapped_column(String(40), default="system")
    published_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)


class GuidelineImport(Base):
    __tablename__ = "guideline_imports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | error
    proposed: Mapped[int] = mapped_column(Integer, default=0)
    dropped_uncited: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    statement: Mapped[str] = mapped_column(Text)  # canonical premise used for verification
    approved_phrasing: Mapped[str] = mapped_column(Text, default="")  # drop-in wording for fixes
    claim_class: Mapped[str] = mapped_column(String(20), default="general")
    subject: Mapped[str] = mapped_column(String(120), default="")  # e.g. product name
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="")
    owner: Mapped[str] = mapped_column(String(80), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    valid_from: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    superseded_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    verified_at: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    def status_on(self, day: dt.date) -> str:
        if self.retired:
            return "retired"
        if self.valid_from and day < self.valid_from:
            return "scheduled"
        if self.valid_until and day > self.valid_until:
            return "expired"
        return "active"


class ContentPR(Base):
    __tablename__ = "cprs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(20))
    author_type: Mapped[str] = mapped_column(String(20), default="human")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft | in_review | changes_requested | approved
    confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    intent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requires_claims_review: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    receipt: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)

    author: Mapped[User] = relationship()
    versions: Mapped[list["CPRVersion"]] = relationship(
        back_populates="cpr", order_by="CPRVersion.number", cascade="all, delete-orphan"
    )


class CPRVersion(Base):
    __tablename__ = "cpr_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpr_id: Mapped[int] = mapped_column(ForeignKey("cprs.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="paste")  # paste | docx | pdf | md | fix | edit
    filename: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    run_status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | running | done | error
    run_progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_error: Mapped[str] = mapped_column(Text, default="")
    policy_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ledger_hash: Mapped[str] = mapped_column(String(64), default="")
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ran_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    run_ms: Mapped[int] = mapped_column(Integer, default=0)

    cpr: Mapped[ContentPR] = relationship(back_populates="versions")
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="version", order_by="Finding.start", cascade="all, delete-orphan"
    )


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpr_id: Mapped[int] = mapped_column(ForeignKey("cprs.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("cpr_versions.id"), index=True)
    check: Mapped[str] = mapped_column(String(12))  # terms | claims | voice | a11y | format
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="open")
    # open | accepted | disputed | upheld | overridden | resolved
    rule_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fact_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    policy_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    citation: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(240))
    message: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[int] = mapped_column(Integer, default=0)
    end: Mapped[int] = mapped_column(Integer, default=0)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    fix_replacement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fix_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    fix_note: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(12), default="rule")  # rule | nli | llm | heuristic
    verdict: Mapped[Optional[str]] = mapped_column(String(14), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    carried_from: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    version: Mapped[CPRVersion] = relationship(back_populates="findings")


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpr_id: Mapped[int] = mapped_column(ForeignKey("cprs.id"), index=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    rule_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fact_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(12))  # accept | edit | dispute | uphold | override
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpr_id: Mapped[int] = mapped_column(ForeignKey("cprs.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("cpr_versions.id"))
    reviewer_id: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(20))  # approve | request_changes
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpr_id: Mapped[int] = mapped_column(ForeignKey("cprs.id"), index=True)
    version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finding_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    author_id: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="comment")  # comment | event
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class AICall(Base):
    __tablename__ = "ai_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    cpr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    import_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(60))
    location: Mapped[str] = mapped_column(String(12))  # external | local | none
    status: Mapped[str] = mapped_column(String(12))  # ok | cached | error | skipped | invalid
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    prompt_preview: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class LLMCache(Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
