"""The LLM path with a fake Gemini: model output is never trusted blindly."""
import json

import httpx

from app import seed_data as sd
from app.config import settings
from app.db import SessionLocal
from app.engine import intent
from app.engine.runner import analyse
from app.models import AICall
from tests.test_engine import facts
from app.engine.core import RunContext

SENT: list[dict] = []


class FakeResp:
    def __init__(self, obj):
        self.status_code = 200
        self._obj = obj
        self.text = json.dumps(obj)

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self._obj)}]}}]}


def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002
    SENT.append(json)
    system = json["systemInstruction"]["parts"][0]["text"]
    if "brand-voice reviewer" in system:
        return FakeResp({"issues": [
            {"rule_key": "V-4", "span": "now", "reason": "urgency", "suggestion": "today", "confidence": 0.9},
            {"rule_key": "V-99", "span": "Meet", "reason": "invented rule", "suggestion": "", "confidence": 0.9},  # unknown rule
            {"rule_key": "V-1", "span": "text that is not there", "reason": "hallucinated", "suggestion": "", "confidence": 0.9},
        ]})
    if "extract checkable claims" in system:
        return FakeResp({"claims": [
            {"span": "100% natural", "claim": "Aloe Calm Gel is 100% natural", "claim_class": "environmental"},
            {"span": "made on the moon", "claim": "made on the moon", "claim_class": "general"},  # not in text
        ]})
    if "summarise the creator's intent" in system:
        return FakeResp({"goal": "Launch", "audience": "Skincare buyers", "cta": "Grab it now", "key_message": "New gel", "must_keep": ["Aloe Gel"]})
    return FakeResp({"judgments": [], "fixes": []})


def test_llm_output_is_validated(monkeypatch):
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_disabled", False)
    s = next(x for x in sd.SAMPLES if x["id"] == "launch-caption")
    text = s["text"] + " Questions? Email asha@kora.in"
    ctx = RunContext(text=text, channel="instagram", author_type="agency", rules=sd.RULES, facts=facts(), policy_version=2, use_llm=True,
                     llm_ctx={"cpr_id": 999, "version_id": 999, "confidential": False})
    ctx.intent = intent.extract(ctx)
    assert ctx.intent["source"] == "llm"
    ds, claims, summ = analyse(ctx)
    voice_llm = [d for d in ds if d.check == "voice" and d.source == "llm"]
    assert [ctx.text[d.start:d.end] for d in voice_llm] == ["now"]  # invented rule + hallucinated span discarded
    assert all(d.severity in {"warn", "suggest"} for d in voice_llm)
    assert not any("moon" in c.text for c in claims)
    assert any(c.text == "100% natural" and c.verdict == "contradicted" for c in claims)
    # personal data never left the server
    assert SENT and all("asha@kora.in" not in json.dumps(b) for b in SENT)
    db = SessionLocal()
    try:
        calls = db.query(AICall).filter(AICall.cpr_id == 999).all()
        assert calls and any(c.redactions >= 1 for c in calls if c.status == "ok")
    finally:
        db.close()
