"""The single LLM gateway. Every model call in Plumb goes through `call()`:

  redact -> (cache) -> route -> call Gemini with a strict JSON schema -> validate -> restore -> log

Nothing else in the codebase talks to a model provider, which is what makes
redaction, confidential routing and the AI disclosure log enforceable.
If the LLM is unavailable the caller gets None and falls back to deterministic
logic -- the product keeps working, with fewer judgment-based findings."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import AICall, LLMCache, Setting
from .redact import Redactor

log = logging.getLogger("plumb.llm")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Ctx:
    purpose: str
    cpr_id: Optional[int] = None
    version_id: Optional[int] = None
    import_id: Optional[int] = None
    confidential: bool = False


def confidential_terms(db: Session) -> list[str]:
    row = db.get(Setting, "confidential_terms")
    return list(row.value) if row and isinstance(row.value, list) else []


def _log(ctx: Ctx, **kw: Any) -> None:
    db = SessionLocal()
    try:
        db.add(AICall(purpose=ctx.purpose, cpr_id=ctx.cpr_id, version_id=ctx.version_id, import_id=ctx.import_id, **kw))
        db.commit()
    except Exception:  # logging must never break a run
        db.rollback()
        log.exception("failed to write AI call log")
    finally:
        db.close()


def status() -> dict:
    return {
        "enabled": settings.llm_enabled,
        "provider": "google-gemini" if settings.llm_enabled else "none",
        "model": settings.gemini_model if settings.llm_enabled else "",
        "reason": "" if settings.llm_enabled else ("PLUMB_LLM_DISABLED=1" if settings.llm_disabled else "no GEMINI_API_KEY set"),
    }


def call(ctx: Ctx, system: str, user: str, schema: dict[str, Any], *, temperature: float = 0.0) -> Optional[dict[str, Any]]:
    """Return the model's JSON object, or None if unavailable/failed/blocked."""
    if ctx.confidential:
        _log(ctx, provider="none", model="", location="none", status="skipped", note="Confidential content: external model calls are blocked; deterministic checks and local NLI only.")
        return None
    if not settings.llm_enabled:
        _log(ctx, provider="none", model="", location="none", status="skipped", note=f"LLM unavailable ({status()['reason']}); deterministic checks and local NLI only.")
        return None

    db = SessionLocal()
    try:
        red = Redactor(confidential_terms(db))
        user_r = red.redact(user)
        system_r = red.redact(system)
        payload_text = system_r + "\n\n" + user_r
        sha = hashlib.sha256((settings.gemini_model + "|" + json.dumps(schema, sort_keys=True) + "|" + payload_text).encode()).hexdigest()
        cached = db.get(LLMCache, sha)
        if cached is not None:
            _log(ctx, provider="google-gemini", model=settings.gemini_model, location="external", status="cached", redactions=red.count,
                 prompt_chars=len(payload_text), prompt_sha256=sha, prompt_preview=user_r[:600], note="Served from cache (same prompt, same model).")
            return red.restore(cached.response)
    finally:
        db.close()

    body = {
        "systemInstruction": {"parts": [{"text": system_r}]},
        "contents": [{"role": "user", "parts": [{"text": user_r}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json", "responseSchema": schema},
    }
    models = [settings.gemini_model] + [m for m in settings.gemini_fallback_models if m != settings.gemini_model]
    t0 = time.time()
    last_err = ""
    for model in models:
        for attempt in range(3):
            try:
                r = httpx.post(API.format(model=model), json=body, timeout=settings.llm_timeout_s,
                               headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"})
            except httpx.HTTPError as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                last_err = f"model {model} not found"
                break  # try next model
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:300]}"
                break
            try:
                data = r.json()
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                obj = json.loads(text)
                if not isinstance(obj, dict):
                    raise ValueError("response is not a JSON object")
            except Exception as e:
                _log(ctx, provider="google-gemini", model=model, location="external", status="invalid", redactions=red.count,
                     prompt_chars=len(payload_text), prompt_sha256=sha, prompt_preview=user_r[:600],
                     latency_ms=int((time.time() - t0) * 1000), note=f"Unparseable output discarded: {e}")
                return None
            db = SessionLocal()
            try:
                db.merge(LLMCache(key=sha, response=obj))
                db.commit()
            finally:
                db.close()
            _log(ctx, provider="google-gemini", model=model, location="external", status="ok", redactions=red.count,
                 prompt_chars=len(payload_text), prompt_sha256=sha, prompt_preview=user_r[:600], latency_ms=int((time.time() - t0) * 1000),
                 note=f"{red.count} value(s) redacted before sending." if red.count else "")
            return red.restore(obj)
        else:
            continue
    _log(ctx, provider="google-gemini", model=settings.gemini_model, location="external", status="error", redactions=red.count,
         prompt_chars=len(payload_text), prompt_sha256=sha, prompt_preview=user_r[:600], latency_ms=int((time.time() - t0) * 1000),
         note=f"Call failed, fell back to deterministic logic: {last_err}")
    return None


# ---- schema helpers (Gemini responseSchema uses an OpenAPI subset with upper-case types)

def S(t: str, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"type": t.upper()}
    d.update(kw)
    return d


def OBJ(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "OBJECT", "properties": props, "required": required or list(props.keys())}


def ARR(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "ARRAY", "items": items}
