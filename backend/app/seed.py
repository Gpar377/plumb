"""Seed the demo workspace (Kora Naturals) and two worked examples."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import seed_data as sd
from .db import SessionLocal
from .models import ContentPR, Fact, PolicyVersion, Rule, Setting, User

log = logging.getLogger("plumb.seed")


def _snap(rules: list[Rule]) -> list[dict]:
    return [{"key": r.key, "type": r.type, "title": r.title, "severity": r.severity, "params": r.params, "section": r.section,
             "source_quote": r.source_quote} for r in sorted(rules, key=lambda r: (r.type, r.key))]


def seed_base(db: Session) -> bool:
    if db.scalar(select(User).limit(1)) is not None:
        return False
    for u in sd.USERS:
        db.add(User(**u))
    rules = [Rule(status="approved", origin="seed", decided_by="meera", **r) for r in sd.RULES]
    db.add_all(rules)
    for f in sd.FACTS:
        db.add(Fact(**f))
    db.add(PolicyVersion(number=1, note="Initial import of the Kora Naturals brand guidelines.", published_by="meera",
                         rules=_snap([r for r in rules if r.key not in sd.V2_ONLY]), is_current=False))
    db.add(PolicyVersion(number=2, note="Added §5.1 greenwashing rule (CCPA 2024 guidelines) and §2.8 fairness-language ban.",
                         published_by="meera", rules=_snap(rules), is_current=True))
    db.add(Setting(key="confidential_terms", value=["Project Monsoon", "Aloe Calm Gel Pro", "Q4 price revision"]))
    db.commit()
    log.info("seeded base workspace")
    return True


def seed_examples() -> None:
    """Two finished examples so the review queue and receipts aren't empty on first visit.
    Runs with the LLM switched off so a restart never spends API quota."""
    from . import services
    from .engine import runner

    db = SessionLocal()
    try:
        if db.scalar(select(ContentPR).limit(1)) is not None:
            return
        users = {u.id: u for u in db.scalars(select(User))}
        samples = {s["id"]: s for s in sd.SAMPLES}

        def run_latest(cpr: ContentPR) -> None:
            db.refresh(cpr)
            v = services.latest(cpr)
            _run_offline(v.id)
            db.expire_all()

        # 1) approved LinkedIn post with a receipt
        s = samples["linkedin-clean"]
        c1 = services.create_cpr(db, users["asha"], title=s["title"], channel=s["channel"], author_type=s["author_type"], text=s["text"])
        run_latest(c1)
        c1 = db.get(ContentPR, c1.id)
        services.update_intent(db, c1, users["asha"], {"goal": "Announce recyclable packaging", "audience": "Existing customers and partners on LinkedIn",
                                                         "cta": "Find your new everyday gel on our website.", "must_keep": ["Aloe Calm Gel", "100% recyclable PET bottle"]})
        services.submit(db, c1, users["asha"])
        services.review(db, c1, users["rohan"], "approve", "All three claims match the ledger (F-11, F-07, F-19).")
        services.review(db, c1, users["meera"], "approve", "On voice. Ship it.")

        # 2) influencer post, three versions, now waiting for review
        s = samples["influencer-post"]
        c2 = services.create_cpr(db, users["bluefin"], title=s["title"], channel=s["channel"], author_type=s["author_type"], text=s["text"])
        run_latest(c2)
        c2 = db.get(ContentPR, c2.id)
        v = services.latest(c2)
        items = [{"finding_id": f.id} for f in v.findings if f.fix_verified and f.fix_replacement is not None]
        services.apply_fixes(db, c2, users["bluefin"], items)
        run_latest(c2)
        c2 = db.get(ContentPR, c2.id)
        v = services.latest(c2)
        text = v.text.replace("It literally cured my acne in a week", "My skin feels calm and soft after a week")
        services.new_version(db, c2, users["bluefin"], text)
        run_latest(c2)
        c2 = db.get(ContentPR, c2.id)
        services.update_intent(db, c2, users["bluefin"], {"goal": "Creator review of Rose Glow Serum", "audience": "Creator's followers, 18–30",
                                                          "cta": "Use code GLOW10 for 10% off", "must_keep": ["Rose Glow Serum", "GLOW10"]})
        services.submit(db, c2, users["bluefin"])
        log.info("seeded example content PRs")
    except Exception:
        log.exception("example seeding failed (the app still works without them)")
        db.rollback()
    finally:
        db.close()


def _run_offline(version_id: int) -> None:
    from .engine import runner

    runner.run_version(version_id, use_llm=False)
