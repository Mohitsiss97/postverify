"""Settings. Everything comes from the environment; no secret has a default here."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """The configuration cannot support a safe start."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # --- application ----------------------------------------------------
    env: str = Field("development", description="development | staging | production")
    log_level: str = "INFO"
    log_json: bool = Field(
        False, description="Set true in production to make logs machine-readable")

    # --- database -------------------------------------------------------
    # SQLite in development, PostgreSQL in production:
    # postgresql+asyncpg://user:pass@host/db
    database_url: str = "sqlite+aiosqlite:///./campaign_portal.db"
    db_echo: bool = False

    # --- verification engine (postverify-api) ---------------------------
    engine_url: str = Field(
        "http://localhost:8200",
        description="Base URL of postverify-api, which the portal calls over HTTP")
    engine_token: str | None = Field(
        None, description="The engine's ACCESS_TOKEN, if it has one set")
    engine_timeout_seconds: float = Field(
        120.0,
        description="Instagram and Facebook run a browser; allow 15s or more")

    # --- verification rules ---------------------------------------------
    submission_window_hours: int = Field(
        24, description="How old a post may be, measured from the moment of "
                        "submission")
    max_assets_to_try: int = Field(
        5, description="How many of a campaign's creatives to try when asset_id "
                       "was not supplied. Each attempt is another engine call, "
                       "and on Instagram a call takes about 15 seconds.")

    # --- worker ---------------------------------------------------------
    worker_enabled: bool = True
    worker_poll_seconds: float = 2.0
    worker_batch_size: int = 2
    max_attempts: int = Field(
        4, description="Retries apply only to technical failures such as the "
                       "engine being down or timing out. A business rejection "
                       "is final and is never retried.")
    retry_base_seconds: int = 30
    stale_lock_minutes: int = Field(
        15, description="A submission left in `verifying` for longer than this is "
                        "assumed to belong to a worker that died, and is returned "
                        "to the queue. It must comfortably exceed "
                        "engine_timeout_seconds, or a slow verification will be "
                        "picked up twice.")

    # --- storage --------------------------------------------------------
    storage_dir: Path = Field(Path("./storage"),
                              description="Creatives and verification evidence")
    max_upload_bytes: int = 25 * 1024 * 1024

    # --- HTTP -----------------------------------------------------------
    cors_origins: str = Field(
        "", description="Comma-separated browser origins allowed to call the "
                        "API. Empty means same-origin only, which is what the "
                        "bundled UI needs.")
    rate_limit_per_minute: int = Field(
        120, description="Per-client cap on submission endpoints. 0 disables it.")
    trust_proxy_headers: bool = Field(
        False, description="Read the client IP from X-Forwarded-For. Only enable "
                           "this behind a proxy you control, or callers can forge "
                           "the header and defeat the rate limiter.")

    # --- authentication (currently optional) ----------------------------
    admin_token: str | None = Field(
        None, description="When set, admin endpoints reject requests without it. "
                          "Leaving it empty leaves them open, which is for "
                          "development only.")
    user_header: str = Field(
        "X-User-Id",
        description="For now the participant's identity arrives in this header. "
                    "Moving to JWT means changing only deps.current_user().")

    @property
    def assets_dir(self) -> Path:
        return self.storage_dir / "assets"

    @property
    def evidence_dir(self) -> Path:
        return self.storage_dir / "evidence"

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_start(self) -> list[str]:
        """Check the configuration. Returns warnings; raises on fatal problems.

        The distinction matters operationally. A warning belongs in the log for
        the operator to weigh. A fatal problem means this process should never
        accept traffic at all — discovering it later, under load, is worse.
        """
        warnings: list[str] = []

        if self.is_production:
            if not self.admin_token:
                raise ConfigError(
                    "ADMIN_TOKEN must be set when ENV=production. The admin "
                    "endpoints create campaigns and override submission "
                    "decisions; left open, anyone who can reach the service can "
                    "approve their own submissions.")
            if self.database_url.startswith("sqlite"):
                raise ConfigError(
                    "DATABASE_URL still points at SQLite. SQLite serialises "
                    "writes across the whole file, so a second worker will fail "
                    "on lock contention. Use PostgreSQL in production.")
            if "*" in self.cors_origin_list:
                raise ConfigError(
                    "CORS_ORIGINS must not be '*'. The portal is authenticated "
                    "by header, and a wildcard origin would let any site issue "
                    "authenticated requests on a participant's behalf.")
            if self.engine_url.startswith("http://") and \
                    "localhost" not in self.engine_url and \
                    "127.0.0.1" not in self.engine_url:
                warnings.append(
                    "ENGINE_URL uses plain http:// to a remote host. Campaign "
                    "creatives and the engine token cross that link in the "
                    "clear; use https:// or keep the hop inside a private "
                    "network.")
            if self.rate_limit_per_minute == 0:
                warnings.append(
                    "RATE_LIMIT_PER_MINUTE is 0, so rate limiting is off. Only "
                    "do this when a proxy in front enforces its own.")
        else:
            if not self.admin_token:
                warnings.append(
                    "ADMIN_TOKEN is not set, so the admin endpoints are open. "
                    "That is fine locally, but it must be set before this is "
                    "publicly reachable.")

        return warnings

    def describe(self) -> dict:
        """The effective configuration, with no secret values in it.

        Logged once at startup: the first question about any misbehaving
        deployment is what it is actually configured with.
        """
        return {
            "env": self.env,
            "database": self.database_url.split("://")[0],
            "engine_url": self.engine_url,
            "engine_token_set": bool(self.engine_token),
            "admin_token_set": bool(self.admin_token),
            "window_hours": self.submission_window_hours,
            "max_assets_to_try": self.max_assets_to_try,
            "worker_enabled": self.worker_enabled,
            "max_attempts": self.max_attempts,
            "stale_lock_minutes": self.stale_lock_minutes,
            "storage_dir": str(self.storage_dir),
            "cors_origins": self.cors_origin_list,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "trust_proxy_headers": self.trust_proxy_headers,
            "log_level": self.log_level,
            "log_json": self.log_json,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
