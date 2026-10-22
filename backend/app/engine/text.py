"""Text utilities: sentence/clause segmentation with character offsets, phrase
matching, and quantity extraction (₹, %, lakh/crore, counts with units)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENT_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+|(?<=[\U0001F300-\U0001FAFF\u2728\u2764\u2600-\u27BF])\s+(?=[A-Z])")


@dataclass
class Span:
    start: int
    end: int
    text: str


def sentences(text: str) -> list[Span]:
    """Split into sentences, keeping exact offsets into the original text."""
    out: list[Span] = []
    pos = 0
    for m in _SENT_END.finditer(text):
        _add(out, text, pos, m.start())
        pos = m.end()
    _add(out, text, pos, len(text))
    return out


def _add(out: list[Span], text: str, a: int, b: int) -> None:
    seg = text[a:b]
    lead = len(seg) - len(seg.lstrip())
    trail = len(seg) - len(seg.rstrip())
    a2, b2 = a + lead, b - trail
    if b2 > a2:
        out.append(Span(a2, b2, text[a2:b2]))


_CLAUSE_SPLIT = re.compile(r"\s*[,;:—–]\s*|\s+-\s+|\s+\band\b\s+|\s+\bor\b\s+|\s+\bbut\b\s+")


def clauses(sent: Span) -> list[Span]:
    out: list[Span] = []
    pos = 0
    t = sent.text
    for m in _CLAUSE_SPLIT.finditer(t):
        _add_rel(out, sent, pos, m.start())
        pos = m.end()
    _add_rel(out, sent, pos, len(t))
    return out


def _add_rel(out: list[Span], sent: Span, a: int, b: int) -> None:
    seg = sent.text[a:b]
    lead = len(seg) - len(seg.lstrip())
    trail = len(seg) - len(seg.rstrip(" .!?"))
    a2, b2 = a + lead, b - trail
    if b2 > a2:
        out.append(Span(sent.start + a2, sent.start + b2, sent.text[a2:b2]))


def containing(spans: list[Span], start: int, end: int) -> Span | None:
    for s in spans:
        if s.start <= start and end <= s.end:
            return s
    for s in spans:  # partial overlap fallback
        if s.start < end and start < s.end:
            return s
    return None


def phrase_regex(phrase: str, case_sensitive: bool = False) -> re.Pattern[str]:
    """Match a phrase on word-ish boundaries. Apostrophes are matched loosely
    (’ vs ') and inner whitespace is flexible."""
    parts = []
    for ch in phrase:
        if ch in "'’":
            parts.append("['’]")
        elif ch.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(ch))
    body = "".join(parts)
    left = r"(?<![\w-])" if (phrase[:1].isalnum()) else r"(?<!\w)" if phrase[:1] not in "#₹" else ""
    right = r"(?![\w-])" if (phrase[-1:].isalnum()) else ""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(left + body + right, flags)


def find_phrases(text: str, phrases: list[str], case_sensitive: bool = False) -> list[Span]:
    hits: list[Span] = []
    for p in phrases:
        if not p:
            continue
        for m in phrase_regex(p, case_sensitive).finditer(text):
            hits.append(Span(m.start(), m.end(), m.group(0)))
    return longest_non_overlapping(hits)


def longest_non_overlapping(spans: list[Span]) -> list[Span]:
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    for s in spans:
        if out and s.start < out[-1].end:
            if (s.end - s.start) > (out[-1].end - out[-1].start):
                out[-1] = s
            continue
        out.append(s)
    return out


def match_case(replacement: str, original: str) -> str:
    if not replacement:
        return replacement
    if original.isupper() and len(original) > 1 and replacement.lower() == replacement:
        return replacement  # don't shout the fix; ALL-CAPS is itself a finding
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    if original[:1].islower() and replacement[:1].isupper() and replacement.split(" ")[0].lower() == replacement.split(" ")[0].lower() and not any(c.isupper() for c in replacement[1:]):
        return replacement[:1].lower() + replacement[1:]
    return replacement


# ---------------------------------------------------------------- quantities

_MULT = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "k": 1e3, "thousand": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6,
}
_CUR = re.compile(r"(?:₹|\bRs\.?|\bINR)\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(lakh|lakhs|crore|crores|k|K)\b)?", re.IGNORECASE)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_NUM = re.compile(
    r"(?<![\w₹#.])(\d[\d,]*(?:\.\d+)?)(\+)?\s?(lakh|lakhs|lac|lacs|crore|crores|cr|k|thousand|million|mn)?\b(\+)?",
    re.IGNORECASE,
)
_UNIT_WORDS = re.compile(r"^\s*(?:of\s+)?([A-Za-z][A-Za-z\- ]{0,30})")


@dataclass
class Quantity:
    value: float
    kind: str  # inr | pct | count
    unit: str  # e.g. "inr", "%", "hours", "customers", "pin codes"
    start: int
    end: int
    raw: str


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def quantities(text: str) -> list[Quantity]:
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in _CUR.finditer(text):
        v = _num(m.group(1)) * _MULT.get((m.group(2) or "").lower(), 1)
        out.append(Quantity(v, "inr", "inr", m.start(), m.end(), m.group(0)))
        taken.append((m.start(), m.end()))
    for m in _PCT.finditer(text):
        if free(m.start(), m.end()):
            out.append(Quantity(_num(m.group(1)), "pct", "%", m.start(), m.end(), m.group(0)))
            taken.append((m.start(), m.end()))
    for m in _NUM.finditer(text):
        if not free(m.start(), m.end()):
            continue
        raw_n = m.group(1)
        mult = (m.group(3) or "").lower()
        # skip years, codes, list numbers and times
        if not mult and re.fullmatch(r"(19|20)\d\d", raw_n):
            continue
        tail = text[m.end(): m.end() + 40]
        um = _UNIT_WORDS.match(tail)
        unit = um.group(1).strip().lower() if um else ""
        unit = re.split(r"\b(?:in|across|for|with|on|from|to|and|already|now|who|that|this|of)\b", unit)[0].strip()
        if not unit and not mult:
            continue
        v = _num(raw_n) * _MULT.get(mult, 1)
        end = m.start() + len(m.group(0).rstrip())
        out.append(Quantity(v, "count", unit or "count", m.start(), end, text[m.start():end]))
        taken.append((m.start(), end))
    out.sort(key=lambda q: q.start)
    return out


def units_compatible(q: Quantity, fact_unit: str) -> bool:
    fu = (fact_unit or "").lower().strip()
    if not fu:
        return False
    if q.kind == "inr":
        return fu in {"inr", "₹", "rs"}
    if q.kind == "pct":
        return fu == "%"
    if fu in {"inr", "%"}:
        return False
    a = set(re.findall(r"[a-z]+", q.unit))
    b = set(re.findall(r"[a-z]+", fu))
    a |= {w.rstrip("s") for w in a}
    b |= {w.rstrip("s") for w in b}
    return bool(a & b)


def format_inr(v: float) -> str:
    return f"₹{int(v):,}" if float(v).is_integer() else f"₹{v:,.2f}"


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U00002B00-\U00002BFF❤✨]"
)


def emoji_spans(text: str) -> list[Span]:
    return [Span(m.start(), m.end(), m.group(0)) for m in _EMOJI.finditer(text)]


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> tuple[str, list[tuple[int, int, str]]]:
    """Apply non-overlapping (start, end, replacement) edits. Returns new text and
    the edits that were skipped because they overlapped an earlier one."""
    edits = sorted(edits, key=lambda e: (e[0], e[1]))
    kept: list[tuple[int, int, str]] = []
    skipped: list[tuple[int, int, str]] = []
    last_end = -1
    for e in edits:
        if e[0] < last_end:
            skipped.append(e)
            continue
        kept.append(e)
        last_end = max(last_end, e[1])
    out = text
    for a, b, r in reversed(kept):
        out = out[:a] + r + out[b:]
    return tidy(out), skipped


def tidy(text: str) -> str:
    """Clean artefacts left by removals: double spaces, space before punctuation,
    dangling 'and ,' etc."""
    t = re.sub(r"[ \t]{2,}", " ", text)
    t = re.sub(r" +([,.;:!?])", r"\1", t)
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"\b(and|or)\s+(and|or)\b", r"\1", t)
    t = re.sub(r",\s+and\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s+(and|or)([.!?])", r"\2", t)
    t = re.sub(r"(—|–)\s*([,.;])", r"\2", t)
    return t


# ---------------------------------------------------------------- readability

_VOWELS = re.compile(r"[aeiouy]+")


def syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(_VOWELS.findall(w)))


def fk_grade(text: str) -> float:
    """Flesch-Kincaid grade level with a deterministic syllable heuristic."""
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    if not words:
        return 0.0
    n_sent = max(1, len([s for s in sentences(text) if re.search(r"[A-Za-z]", s.text)]))
    n_syl = sum(syllables(w) for w in words)
    return round(0.39 * (len(words) / n_sent) + 11.8 * (n_syl / len(words)) - 15.59, 1)
