"""Centralised, typed configuration for VaaniSetu AI.

All values can be overridden through environment variables or a ``.env`` file in the
project root (see ``.env.example``). Access settings through :func:`get_settings`, which
caches a single instance per process.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- OpenAI ----------
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"  # reasoning: routing, slot extraction, grounded answers
    openai_small_model: str = "gpt-4o-mini"  # cheap semantic checks (confirmation, translation)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dim: int = 1536
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2

    # --- Voice ----------
    speech_provider: Literal["openai", "bhashini"] = Field(
        default="openai",
        description="'openai' uses your OpenAI key (no extra sign-up); 'bhashini' needs ULCA keys.",
    )
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    max_audio_mb: int = 10

    # --- Bhashini (optional speech provider: ULCA config + Dhruva inference) ----------
    bhashini_user_id: str | None = None
    bhashini_ulca_api_key: SecretStr | None = None
    bhashini_pipeline_id: str = "64392f96daac500b55c543cd"  # MeitY pipeline
    bhashini_config_url: str = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
    bhashini_timeout_s: float = 20.0
    bhashini_config_ttl_s: int = 3600
    bhashini_tts_gender: str = "female"
    audio_sample_rate: int = 16000

    # --- Retrieval ----------
    qdrant_path: Path = PROJECT_ROOT / "qdrant_data"
    qdrant_url: str | None = None  # set to use a Qdrant server instead of local disk mode
    qdrant_collection: str = "vaanisetu_policies"
    policies_dir: Path = PROJECT_ROOT / "data" / "raw_policies"
    retrieval_top_k: int = 8  # measured: 8 passages lift hit-rate 19/22 -> 20/22 over 5
    retrieval_candidate_k: int = 20
    rrf_k: int = 60

    # --- Guardrails ----------
    pii_score_threshold: float = 0.5

    # --- Telemetry / agent ----------
    trace_log_path: Path = PROJECT_ROOT / "logs" / "traces.jsonl"
    default_language: str = Field(
        default="auto", description="'auto' replies in the user's language; or en/hi/hinglish/..."
    )

    # --- Web application ----------
    host: str = "127.0.0.1"
    port: int = 8000
    database_path: Path = PROJECT_ROOT / "data" / "app.db"
    checkpoint_path: Path = PROJECT_ROOT / "data" / "conversations.db"  # LangGraph state
    user_files_dir: Path = PROJECT_ROOT / "data" / "user_files"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    session_days: int = Field(default=14, description="How long a sign-in stays valid.")
    cookie_secure: bool = Field(default=False, description="Set true when serving over HTTPS.")
    max_photo_mb: int = 3
    secret_key: SecretStr | None = Field(
        default=None, description="Signs one-time email codes. Generated into data/secret.key."
    )

    # --- Email (verification codes and password resets) ----------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = Field(default=None, description='e.g. "VaaniSetu AI <you@gmail.com>"')
    email_code_minutes: int = 10
    email_code_attempts: int = 5
    email_resend_seconds: int = 60

    @property
    def bhashini_enabled(self) -> bool:
        return bool(self.bhashini_user_id and self.bhashini_ulca_api_key)

    @property
    def openai_enabled(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())

    @property
    def email_enabled(self) -> bool:
        """True when SMTP is configured; otherwise codes are printed to the server console."""
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
