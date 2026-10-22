"""Redaction before any external model call. Personal data patterns and the
workspace's confidential-term list are swapped for placeholders; the mapping is
kept locally so model output can be restored to the original wording."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")),
]


@dataclass
class Redactor:
    confidential_terms: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)
    count: int = 0

    def _token(self, kind: str, original: str) -> str:
        for k, v in self.mapping.items():
            if v == original:
                return k
        self.count += 1
        tok = f"[[{kind}_{self.count}]]"
        self.mapping[tok] = original
        return tok

    def redact(self, text: str) -> str:
        out = text
        for term in sorted({t for t in self.confidential_terms if t.strip()}, key=len, reverse=True):
            out = re.sub(re.escape(term), lambda m: self._token("CONFIDENTIAL", m.group(0)), out, flags=re.IGNORECASE)
        for kind, pat in PATTERNS:
            out = pat.sub(lambda m: self._token(kind, m.group(0)), out)
        return out

    def restore(self, value: Any) -> Any:
        if isinstance(value, str):
            for tok, orig in self.mapping.items():
                value = value.replace(tok, orig)
            return value
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        return value
