"""Settings — sab environment se, code me koi default secret nahi."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # --- app -----------------------------------------------------------
    env: str = Field("development", description="development | staging | production")
    log_level: str = "INFO"
    log_json: bool = Field(
        False, description="Production me true — logs machine-readable ho jaate hain")

    # --- database ------------------------------------------------------
    # Dev me SQLite, prod me Postgres: postgresql+asyncpg://user:pass@host/db
    database_url: str = "sqlite+aiosqlite:///./campaign_portal.db"
    db_echo: bool = False

    # --- verification engine (postverify-api) --------------------------
    engine_url: str = Field(
        "http://localhost:8200",
        description="postverify-api ka base URL. Portal usko HTTP se call karta hai.")
    engine_token: str | None = Field(
        None, description="Engine pe ACCESS_TOKEN set ho to yahan wahi daaliye")
    engine_timeout_seconds: float = Field(
        120.0, description="Instagram/Facebook pe browser chalta hai — 15s+ lagte hain")

    # --- verification rules --------------------------------------------
    submission_window_hours: int = Field(
        24, description="Post itne ghante se purani nahi honi chahiye (submit ke waqt se)")
    max_assets_to_try: int = Field(
        5, description="asset_id na diya ho to campaign ke itne creatives try karenge. "
                       "Har try ek engine call hai — Instagram pe ek call ~15s.")

    # --- worker --------------------------------------------------------
    worker_enabled: bool = True
    worker_poll_seconds: float = 2.0
    worker_batch_size: int = 2
    max_attempts: int = Field(
        4, description="Sirf takneeki fail (engine down, timeout) pe retry hota hai. "
                       "Business rejection final hoti hai — uspe retry nahi.")
    retry_base_seconds: int = 30

    # --- storage -------------------------------------------------------
    storage_dir: Path = Field(Path("./storage"), description="Creatives aur evidence")
    max_upload_bytes: int = 25 * 1024 * 1024

    # --- auth (abhi optional) ------------------------------------------
    admin_token: str | None = Field(
        None, description="Set ho to admin endpoints bina iske 401 denge. "
                          "Khali chhod do to khule rehte hain (sirf dev ke liye).")
    user_header: str = Field(
        "X-User-Id", description="Abhi user ki pehchan isi header se aati hai. "
                                 "Aage JWT lagana ho to sirf ye jagah badalni hogi.")

    @property
    def assets_dir(self) -> Path:
        return self.storage_dir / "assets"

    @property
    def evidence_dir(self) -> Path:
        return self.storage_dir / "evidence"

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
