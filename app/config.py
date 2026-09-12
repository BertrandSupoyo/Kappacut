"""Application settings — every knob is an environment variable.

Infra + product limits live here. Engine tuning (models, clip count, taste) stays in
`pipeline.get_cfg()` for now; it moves to env in a later phase.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core infra -------------------------------------------------------
    database_url: str = "postgresql+asyncpg://clipfinder:clipfinder@localhost:5432/clipfinder"
    redis_url: str = "redis://localhost:6379/0"

    # where uploaded videos + rendered clips live (the shared `media` volume in prod)
    media_root: Path = Path("web_data")
    # the static frontend to serve (Phase 7 swaps this for the React build)
    web_dir: Path = Path("web")

    public_base_url: str = "http://localhost:8000"

    # --- auth (wired in Phase 2) -----------------------------------------
    jwt_secret: str = "dev-insecure-change-me"
    verification_token_secret: str = "dev-insecure-change-me-2"
    reset_password_token_secret: str = "dev-insecure-change-me-3"
    cookie_secure: bool = False
    access_token_ttl_seconds: int = 60 * 60  # 1h

    # --- email (Phase 2) ------------------------------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "clipfinder <no-reply@localhost>"
    smtp_tls: bool = False

    # --- engine keys (already read by clipfinder.py via os.environ) -----
    groq_api_key: str = ""
    anthropic_api_key: str = ""

    # --- image-gen providers (app/thumbnails.py, Phase-boost F) ----------
    # Both optional; generate_thumbnail() only routes to providers with a key set. Groq has
    # no image-gen capability (verified), so this app's first image calls go elsewhere.
    together_api_key: str = ""
    replicate_api_key: str = ""

    # --- free-plan quotas (enforced from Phase 5) ----------------------
    quota_minutes_per_month: int = 60
    quota_max_upload_bytes: int = 2 * 1024**3
    quota_storage_bytes: int = 5 * 1024**3
    quota_renders_per_day: int = 40
    quota_concurrent_jobs: int = 1
    quota_projects: int = 25
    global_rate_limit_per_min: int = 120

    # --- generic credit ledger (app/credits.py) — gates any FUTURE paid-API generative
    # feature (image/video gen) separately from the quotas above, which only cover Groq's
    # free-tier text/audio. No feature spends against this yet; it's pure infrastructure
    # until one (e.g. AI thumbnails) opts in. A caller may pass its own `cap` to
    # check_credits() instead of this shared default once it wants a feature-specific limit.
    credit_daily_cap_default: int = 5

    # --- worker / retention (Phase 4 / 5) ----------------------------
    worker_concurrency: int = 2
    # 0 = let ffmpeg auto-detect (fine for one job at a time). Once WORKER_CONCURRENCY > 1,
    # every concurrent ffmpeg process auto-detects the SAME full core count and they fight
    # each other for it — set this so worker_concurrency * ffmpeg_threads ~= vCPU count and
    # every concurrent render gets an equal, predictable share instead of degrading under load.
    ffmpeg_threads: int = 0
    max_global_queue: int = 40
    job_timeout_seconds: int = 3600
    groq_tokens_per_minute: int = 8000       # shared-key TPM budget (free tier)
    retention_warn_days: int = 30
    retention_delete_days: int = 37  # 0 disables

    # --- ops (Phase 8) ------------------------------------------------
    # nightly pg_dump is a dedicated compose service (`backup`), not app code
    disk_min_free_pct: float = 4.0  # health goes red + uploads 503 below this

    environment: str = Field(default="dev")

    @property
    def is_prod(self) -> bool:
        return self.environment.lower() in ("prod", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
