from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from zoneinfo import ZoneInfo

SEVERITY_RANK = {"block": 3, "warn": 2, "suggest": 1}
CHECKS = ["terms", "voice", "claims", "a11y", "format"]


def today() -> dt.date:
    forced = os.getenv("PLUMB_TODAY")
    if forced:
        return dt.date.fromisoformat(forced)
    return dt.datetime.now(ZoneInfo("Asia/Kolkata")).date()


@dataclass
class Draft:
    """A finding before persistence."""

    check: str
    severity: str
    rule_key: Optional[str]
    title: str
    message: str
    start: int
    end: int
    fact_key: Optional[str] = None
    fix: Optional[str] = None
    confidence: float = 1.0
    source: str = "rule"
    verdict: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    fix_verified: bool = False
    fix_note: str = ""

    def overlaps(self, a: int, b: int) -> bool:
        if self.start == self.end:  # insertion point
            return a <= self.start <= b
        return self.start < b and a < self.end


@dataclass
class FactView:
    key: str
    statement: str
    approved_phrasing: str
    claim_class: str
    subject: str
    value: Optional[float]
    unit: str
    owner: str
    evidence: str
    status: str
    valid_until: Optional[str]
    superseded_by: Optional[str]
    verified_at: Optional[str]


@dataclass
class RunContext:
    text: str
    channel: str
    author_type: str
    rules: list[dict[str, Any]]
    facts: list[FactView]
    policy_version: int
    intent: dict[str, Any] = field(default_factory=dict)
    today: dt.date = field(default_factory=today)
    use_llm: bool = True
    llm_ctx: dict[str, Any] = field(default_factory=dict)  # cpr_id/version_id/confidential for the gateway

    def rules_of(self, type_: str) -> list[dict[str, Any]]:
        return [r for r in self.rules if r["type"] == type_]

    def rule(self, key: Optional[str]) -> Optional[dict[str, Any]]:
        return next((r for r in self.rules if r["key"] == key), None) if key else None

    def fact(self, key: Optional[str]) -> Optional[FactView]:
        return next((f for f in self.facts if f.key == key), None) if key else None

    def claim_rule(self, claim_class: str) -> Optional[dict[str, Any]]:
        return next((r for r in self.rules_of("claim") if r["params"].get("claim_class") == claim_class), None)

    def products(self) -> list[str]:
        names = {f.subject for f in self.facts if f.subject}
        for r in self.rules_of("term"):
            rep = r["params"].get("replacement")
            if r["params"].get("mode") == "preferred" and rep and rep[:1].isupper():
                names.add(rep)
        return sorted(names, key=len, reverse=True)

    def canonicalize(self, text: str) -> str:
        """Replace known aliases (e.g. 'Aloe Gel') with canonical names."""
        from .text import find_phrases

        out = text
        for r in self.rules_of("term"):
            p = r["params"]
            if p.get("mode") != "preferred" or not p.get("replacement"):
                continue
            for s in reversed(find_phrases(out, p.get("patterns", []), p.get("case_sensitive", False))):
                out = out[: s.start] + p["replacement"] + out[s.end :]
        return out


def ledger_hash(facts: list[FactView]) -> str:
    blob = json.dumps([f.__dict__ for f in sorted(facts, key=lambda f: f.key)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
