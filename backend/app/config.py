"""Runtime configuration. Everything is read from environment variables so the
same image runs locally, in CI and on the Hugging Face Space."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("PLUMB_DATA_DIR", str(BACKEND_DIR / "data"))))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    gemini_fallback_models: tuple[str, ...] = ("gemini-flash-latest", "gemini-2.0-flash")
    llm_disabled: bool = field(default_factory=lambda: _bool("PLUMB_LLM_DISABLED"))
    ml_disabled: bool = field(default_factory=lambda: _bool("PLUMB_ML_DISABLED"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("PLUMB_LLM_TIMEOUT", "40")))
    receipt_secret: str = field(default_factory=lambda: os.getenv("PLUMB_RECEIPT_SECRET", ""))
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("PLUMB_STATIC_DIR", str(REPO_DIR / "frontend" / "dist"))))
    demo_dir: Path = REPO_DIR / "demo"
    nli_model: str = field(default_factory=lambda: os.getenv("PLUMB_NLI_MODEL", "cross-encoder/nli-deberta-v3-small"))
    embed_model: str = field(default_factory=lambda: os.getenv("PLUMB_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    seed_on_start: bool = field(default_factory=lambda: _bool("PLUMB_SEED", True))
    max_upload_bytes: int = 8 * 1024 * 1024
    max_text_chars: int = 20000

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'plumb.db'}"
        if not self.receipt_secret:
            # Persist a random secret for this data dir so receipts verify across requests.
            p = self.data_dir / ".receipt_secret"
            if p.exists():
                self.receipt_secret = p.read_text().strip()
            else:
                self.receipt_secret = secrets.token_hex(32)
                p.write_text(self.receipt_secret)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.gemini_api_key) and not self.llm_disabled


settings = Settings()
