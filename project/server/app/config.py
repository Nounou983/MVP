"""Environment-driven configuration.

Everything that changes between a laptop and production lives here. No secret
is ever hard-coded: ``CIGOGNE_SECRET_KEY`` is required in production and the
application refuses to start without it.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]


@dataclass(frozen=True)
class Settings:
    """Read at instantiation, never at import.

    Every field goes through `default_factory` on purpose: a dataclass default
    is evaluated once when the module is imported, which would freeze the
    configuration before a .env file or a container's environment is applied.
    """

    env: str = field(default_factory=lambda: os.environ.get("CIGOGNE_ENV", "development"))
    debug: bool = field(default_factory=lambda: _bool("CIGOGNE_DEBUG", False))

    # --- security -------------------------------------------------------
    secret_key: str = field(default_factory=lambda: os.environ.get("CIGOGNE_SECRET_KEY", ""))
    access_token_ttl: int = field(default_factory=lambda: _int("CIGOGNE_ACCESS_TTL", 60 * 30))
    refresh_token_ttl: int = field(default_factory=lambda: _int("CIGOGNE_REFRESH_TTL", 60 * 60 * 24 * 30))
    password_iterations: int = field(default_factory=lambda: _int("CIGOGNE_PBKDF2_ITERATIONS", 240_000))

    # --- database -------------------------------------------------------
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", f"sqlite:///{(SERVER_DIR / 'data' / 'cigogne.db').as_posix()}"))

    # --- storage --------------------------------------------------------
    storage_backend: str = field(default_factory=lambda: os.environ.get("CIGOGNE_STORAGE", "local"))
    storage_dir: str = field(default_factory=lambda: os.environ.get(
        "CIGOGNE_STORAGE_DIR", (SERVER_DIR / "data" / "storage").as_posix()))
    s3_bucket: str = field(default_factory=lambda: os.environ.get("CIGOGNE_S3_BUCKET", ""))
    s3_region: str = field(default_factory=lambda: os.environ.get("CIGOGNE_S3_REGION", ""))
    s3_endpoint: str = field(default_factory=lambda: os.environ.get("CIGOGNE_S3_ENDPOINT", ""))
    s3_public_base: str = field(default_factory=lambda: os.environ.get("CIGOGNE_S3_PUBLIC_BASE", ""))
    signed_url_ttl: int = field(default_factory=lambda: _int("CIGOGNE_SIGNED_URL_TTL", 60 * 60))

    # --- uploads --------------------------------------------------------
    max_image_bytes: int = field(default_factory=lambda: _int("CIGOGNE_MAX_IMAGE_BYTES", 18 * 1024 * 1024))
    max_model_bytes: int = field(default_factory=lambda: _int("CIGOGNE_MAX_MODEL_BYTES", 40 * 1024 * 1024))
    max_state_bytes: int = field(default_factory=lambda: _int("CIGOGNE_MAX_STATE_BYTES", 6 * 1024 * 1024))

    # --- AI worker ------------------------------------------------------
    ai_service_url: str = field(default_factory=lambda: os.environ.get("CIGOGNE_AI_URL", "http://127.0.0.1:8000"))
    gpu_concurrency: int = field(default_factory=lambda: _int("CIGOGNE_GPU_CONCURRENCY", 1))
    job_timeout: int = field(default_factory=lambda: _int("CIGOGNE_JOB_TIMEOUT", 300))
    job_max_attempts: int = field(default_factory=lambda: _int("CIGOGNE_JOB_MAX_ATTEMPTS", 2))
    job_retention_hours: int = field(default_factory=lambda: _int("CIGOGNE_JOB_RETENTION_HOURS", 72))
    worker_poll_seconds: float = field(default_factory=lambda: float(os.environ.get("CIGOGNE_WORKER_POLL", "1.0")))
    inline_worker: bool = field(default_factory=lambda: _bool("CIGOGNE_INLINE_WORKER", True))

    # --- http -----------------------------------------------------------
    cors_origins: list[str] = field(default_factory=lambda: _list(
        "CIGOGNE_CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500"))
    rate_limit_auth: int = field(default_factory=lambda: _int("CIGOGNE_RATE_AUTH", 10))
    rate_limit_ai: int = field(default_factory=lambda: _int("CIGOGNE_RATE_AI", 20))
    rate_limit_write: int = field(default_factory=lambda: _int("CIGOGNE_RATE_WRITE", 120))
    rate_limit_read: int = field(default_factory=lambda: _int("CIGOGNE_RATE_READ", 600))

    # --- product / commerce --------------------------------------------
    products_file: str = field(default_factory=lambda: os.environ.get(
        "CIGOGNE_PRODUCTS_FILE", (SERVER_DIR / "data" / "products.json").as_posix()))

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod", "staging"}

    @property
    def allow_all_origins(self) -> bool:
        return "*" in self.cors_origins


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings. Generates a dev-only key when none is provided."""
    global _settings
    if _settings is not None:
        return _settings

    s = Settings()
    if not s.secret_key:
        if s.is_production:
            raise RuntimeError(
                "CIGOGNE_SECRET_KEY is required when CIGOGNE_ENV=production. "
                "Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\""
            )
        object.__setattr__(s, "secret_key", secrets.token_urlsafe(48))
    if s.is_production and s.allow_all_origins:
        raise RuntimeError(
            "CIGOGNE_CORS_ORIGINS=* is refused in production. List your frontend origins."
        )
    Path(s.storage_dir).mkdir(parents=True, exist_ok=True)
    (SERVER_DIR / "data").mkdir(parents=True, exist_ok=True)
    _settings = s
    return s


def reset_settings_cache() -> None:
    """Test helper — forces the next get_settings() to re-read the environment."""
    global _settings
    _settings = None


# --- plans / entitlements ------------------------------------------------
# Deliberately data, not code: a billing provider can replace this table
# without touching enforcement logic.
PLANS: dict[str, dict] = {
    "free": {
        "label": "Découverte",
        "price_dzd": 0,
        "limits": {
            "projects": 3,
            "ai_jobs_per_month": 40,
            "storage_mb": 200,
            "export_max_side": 1600,
            "collections": 2,
            "share_links": 1,
        },
        "features": {"share": True, "comments": False, "glb_import": True, "hires_export": False},
    },
    "pro": {
        "label": "Pro",
        "price_dzd": 2900,
        "limits": {
            "projects": 60,
            "ai_jobs_per_month": 600,
            "storage_mb": 5000,
            "export_max_side": 4096,
            "collections": 50,
            "share_links": 100,
        },
        "features": {"share": True, "comments": True, "glb_import": True, "hires_export": True},
    },
    "business": {
        "label": "Business",
        "price_dzd": 9900,
        "limits": {
            "projects": 1000,
            "ai_jobs_per_month": 6000,
            "storage_mb": 50000,
            "export_max_side": 6144,
            "collections": 500,
            "share_links": 1000,
        },
        "features": {"share": True, "comments": True, "glb_import": True, "hires_export": True},
    },
}
DEFAULT_PLAN = "free"
