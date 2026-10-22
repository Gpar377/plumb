"""Local models: sentence embeddings for retrieval and a DeBERTa-v3 NLI
cross-encoder for claim entailment. Both run on CPU and never leave the server.
If the models can't load (or PLUMB_ML_DISABLED=1) we fall back to lexical
similarity and 'neutral' entailment, and say so in the run summary."""
from __future__ import annotations

import logging
import re
import threading
from typing import Sequence

import numpy as np

from ..config import settings

log = logging.getLogger("plumb.ml")

_lock = threading.Lock()
_state: dict = {"embed": None, "nli_tok": None, "nli_model": None, "labels": None, "error": "", "loaded": False}


def load() -> bool:
    if settings.ml_disabled:
        _state["error"] = "disabled by PLUMB_ML_DISABLED"
        return False
    with _lock:
        if _state["loaded"]:
            return _state["embed"] is not None
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
            _state["embed"] = SentenceTransformer(settings.embed_model, device="cpu")
            _state["nli_tok"] = AutoTokenizer.from_pretrained(settings.nli_model)
            m = AutoModelForSequenceClassification.from_pretrained(settings.nli_model)
            m.eval()
            _state["nli_model"] = m
            _state["labels"] = {int(k): v.lower() for k, v in m.config.id2label.items()}
            log.info("ML models loaded: %s, %s", settings.embed_model, settings.nli_model)
        except Exception as e:  # pragma: no cover - environment dependent
            log.exception("ML models failed to load")
            _state["error"] = f"{type(e).__name__}: {e}"
        _state["loaded"] = True
        return _state["embed"] is not None


def status() -> dict:
    return {
        "loaded": _state["loaded"] and _state["embed"] is not None,
        "embed_model": settings.embed_model,
        "nli_model": settings.nli_model,
        "error": _state["error"],
    }


def available() -> bool:
    return load()


_tok = re.compile(r"[a-z0-9₹%]+")


def _lex(s: str) -> set[str]:
    return {w for w in _tok.findall(s.lower()) if len(w) > 2 or w.isdigit()}


def embed(texts: Sequence[str]) -> np.ndarray:
    if available():
        v = _state["embed"].encode(list(texts), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)
    # lexical fallback: bag-of-words hashed into 512 dims
    out = np.zeros((len(texts), 512), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in _lex(t):
            out[i, hash(w) % 512] += 1.0
        n = np.linalg.norm(out[i])
        if n:
            out[i] /= n
    return out


def nli(pairs: Sequence[tuple[str, str]]) -> list[dict[str, float]]:
    """Return [{'entailment': p, 'contradiction': p, 'neutral': p}] for (premise, hypothesis)."""
    if not pairs:
        return []
    if not available():
        return [{"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0} for _ in pairs]
    import torch

    tok, model, labels = _state["nli_tok"], _state["nli_model"], _state["labels"]
    out: list[dict[str, float]] = []
    with torch.inference_mode():
        for i in range(0, len(pairs), 16):
            batch = pairs[i : i + 16]
            enc = tok([p for p, _ in batch], [h for _, h in batch], padding=True, truncation=True, max_length=256, return_tensors="pt")
            probs = torch.softmax(model(**enc).logits, dim=-1).numpy()
            for row in probs:
                out.append({labels[j]: float(row[j]) for j in range(len(row))})
    return out
