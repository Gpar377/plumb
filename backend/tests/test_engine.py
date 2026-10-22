"""Engine-level tests: each check type, the claims ledger, fixes, and the safety contracts."""
from pathlib import Path

import pytest

from app import seed_data as sd
from app.engine import intent
from app.engine.core import FactView, RunContext, today
from app.engine.runner import analyse
from app.engine.text import apply_edits, quantities
from app.llm.redact import Redactor
from app.models import Fact


def facts(overrides=None):
    out = []
    for f in sd.FACTS:
        d = dict(f)
        d.update((overrides or {}).get(d["key"], {}))
        fo = Fact(**d)
        out.append(FactView(key=fo.key, statement=fo.statement, approved_phrasing=fo.approved_phrasing, claim_class=fo.claim_class,
                            subject=fo.subject, value=fo.value, unit=fo.unit or "", owner=fo.owner, evidence=fo.evidence,
                            status=fo.status_on(today()), valid_until=str(fo.valid_until) if fo.valid_until else None,
                            superseded_by=fo.superseded_by, verified_at=str(fo.verified_at)))
    return out


def run(text, channel="instagram", author_type="human", rules=None, fs=None):
    ctx = RunContext(text=text, channel=channel, author_type=author_type, rules=rules or sd.RULES, facts=fs or facts(), policy_version=2, use_llm=False)
    ctx.intent = intent.heuristic(ctx)
    return analyse(ctx)


def keys(ds):
    return {(d.rule_key, d.fact_key, d.verdict) for d in ds}


def sample(sid):
    return next(s for s in sd.SAMPLES if s["id"] == sid)


def test_every_seed_rule_quotes_the_guideline():
    doc = (Path(__file__).resolve().parents[2] / "demo" / "kora-brand-guidelines.md").read_text()
    for r in sd.RULES:
        assert r["source_quote"] in doc, f"{r['key']} quote not found in guideline"


def test_launch_caption_finds_the_four_blockers():
    s = sample("launch-caption")
    ds, claims, summ = run(s["text"], s["channel"], s["author_type"])
    k = keys(ds)
    assert ("C-1", "F-07", "contradicted") in k  # 100% natural vs 96% naturally derived
    assert ("C-1", None, "unsupported") in k  # eco-friendly
    assert ("C-3", None, "unsupported") in k  # India's #1
    assert ("C-4", "F-15", "expired") in k  # ₹299 launch price expired
    assert summ["counts"]["block"] == 4
    exp = next(d for d in ds if d.verdict == "expired")
    assert exp.fix == "₹349" and exp.fix_verified
    assert exp.extra["replacement_fact"] == "F-22"
    assert any(d.rule_key == "V-4" and d.severity == "warn" for d in ds)


def test_supported_claims_pass_and_produce_no_findings():
    s = sample("linkedin-clean")
    ds, claims, summ = run(s["text"], s["channel"], s["author_type"])
    assert summ["counts"]["block"] == 0
    assert {c.fact_key for c in claims if c.verdict == "supported"} >= {"F-11", "F-07", "F-19"}


def test_lakh_and_hours_statistics_are_compared_exactly():
    ds, claims, _ = run("Over 5 lakh customers trust Kora Naturals. Aloe Calm Gel gives up to 48 hours of hydration.", "email")
    by_fact = {d.fact_key: d for d in ds if d.verdict == "contradicted"}
    assert by_fact["F-21"].fix == "2 lakh"
    assert by_fact["F-20"].fix == "24"


def test_free_shipping_price_matches_the_right_fact():
    ds, claims, _ = run("Enjoy free shipping on orders above ₹499.", "email")
    c = next(c for c in claims if "499" in c.text)
    assert c.verdict == "supported" and c.fact_key == "F-18"


def test_expiring_a_fact_flips_claims_to_expired():
    fs = facts({"F-19": {"valid_until": __import__("datetime").date(2026, 10, 1)}})
    ds, claims, _ = run("Kora Naturals ships to more than 19,000 PIN codes across India.", "linkedin", fs=fs)
    assert any(d.verdict == "expired" and d.fact_key == "F-19" for d in ds)


def test_medical_claim_blocks_and_influencer_needs_disclosure():
    s = sample("influencer-post")
    ds, _, _ = run(s["text"], s["channel"], s["author_type"])
    assert any(d.rule_key == "C-2" and d.severity == "block" for d in ds)
    disc = next(d for d in ds if d.rule_key == "P-1")
    assert disc.fix == " #ad" and disc.fix_verified
    # the same post from an in-house author does not need #ad
    ds2, _, _ = run(s["text"], s["channel"], "human")
    assert not any(d.rule_key == "P-1" for d in ds2)


def test_fairness_language_is_blocked():
    ds, _, _ = run("Get fairer skin in 7 days with our whitening cream.")
    assert sum(1 for d in ds if d.rule_key == "T-8" and d.severity == "block") == 2


def test_voice_never_blocks():
    ds, _, _ = run("Hurry! Leverage our revolutionary gel. Last chance, ladies!")
    assert all(d.severity != "block" for d in ds if d.check == "voice")


def test_every_finding_cites_a_policy_rule():
    rule_keys = {r["key"] for r in sd.RULES}
    fact_keys = {f["key"] for f in sd.FACTS}
    for s in sd.SAMPLES:
        ds, _, _ = run(s["text"], s["channel"], s["author_type"])
        for d in ds:
            assert d.rule_key in rule_keys
            assert d.fact_key is None or d.fact_key in fact_keys


def test_uncited_findings_are_dropped():
    rules = [r for r in sd.RULES if r["key"] != "C-3"]  # remove the superlative rule
    ds, _, summ = run("India's #1 gel.", rules=rules)
    assert all(d.rule_key != "C-3" for d in ds)


def test_deterministic_runs_are_identical():
    s = sample("festive-email")
    a, _, _ = run(s["text"], s["channel"], s["author_type"])
    b, _, _ = run(s["text"], s["channel"], s["author_type"])
    assert [(d.rule_key, d.start, d.end, d.fix) for d in a] == [(d.rule_key, d.start, d.end, d.fix) for d in b]


def test_verified_fixes_resolve_without_new_blockers():
    s = sample("launch-caption")
    ds, _, _ = run(s["text"], s["channel"], s["author_type"])
    edits = [(d.start, d.end, d.fix) for d in ds if d.fix_verified and d.fix is not None]
    fixed, skipped = apply_edits(s["text"], edits)
    ds2, _, summ2 = run(fixed, s["channel"], s["author_type"])
    assert summ2["counts"]["block"] == 0, [(d.rule_key, d.title) for d in ds2]
    assert "Aloe Calm Gel" in fixed and "₹349" in fixed


def test_intent_guard_rejects_fix_that_drops_cta():
    ctx = RunContext(text="Shop the Aloe Calm Gel today.", channel="web", author_type="human", rules=sd.RULES, facts=facts(), policy_version=2,
                     use_llm=False, intent={"must_keep": ["Aloe Calm Gel"]})
    ok, why = intent.guard(ctx, ctx.text, "The gel today.")
    assert not ok


@pytest.mark.parametrize("text,value,kind", [("₹1,299", 1299, "inr"), ("Rs. 499", 499, "inr"), ("2.5 lakh customers", 250000, "count"),
                                             ("1 crore users", 1e7, "count"), ("96%", 96, "pct")])
def test_quantities(text, value, kind):
    q = quantities(text)[0]
    assert q.value == value and q.kind == kind


def test_redaction_restores_values():
    r = Redactor(["Project Monsoon"])
    red = r.redact("Email asha@kora.in or call +91 98765 43210 about Project Monsoon.")
    assert "asha@kora.in" not in red and "98765" not in red and "Monsoon" not in red
    assert r.restore(red) == "Email asha@kora.in or call +91 98765 43210 about Project Monsoon."
