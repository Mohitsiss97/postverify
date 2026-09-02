"""Every environment variable this service reads, in one place.

The accessors read the environment on each call rather than caching at import
time. That keeps configuration overridable in tests and, more importantly,
means a container that is restarted with new environment values behaves as the
operator expects.

`validate()` runs once at startup. In production it refuses to start on a
configuration that would be unsafe, rather than discovering the problem later
under load.
"""
from __future__ import annotations

import os

APP_NAME = "PostVerify API"
APP_VERSION = "1.0.0"


class ConfigError(RuntimeError):
    """The configuration cannot support a safe start."""


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --- deployment ---------------------------------------------------------

def env() -> str:
    """Deployment environment: development | staging | production."""
    return _str("ENV", "development").lower()


def is_production() -> bool:
    return env() == "production"


def log_level() -> str:
    return _str("LOG_LEVEL", "INFO").upper()


def json_logs() -> bool:
    """Structured JSON log lines. Defaults on in production, off locally."""
    return _bool("JSON_LOGS", is_production())


# --- access control -----------------------------------------------------

def access_token() -> str:
    """Shared secret for the expensive endpoints. Empty means the API is open."""
    return _str("ACCESS_TOKEN")


def cors_origins() -> list[str]:
    """Allowed browser origins. Empty (the default) means no browser access.

    This API is designed to be called server-to-server. Only widen this if a
    browser really must call it directly, and never to "*" while ACCESS_TOKEN
    is in use — that would hand the token to every site the user visits.
    """
    raw = _str("CORS_ORIGINS")
    return [o.strip() for o in raw.split(",") if o.strip()]


# --- rate limiting ------------------------------------------------------

def rate_limit_per_minute() -> int:
    """Requests per minute per client on the expensive endpoints. 0 disables it."""
    return _int("RATE_LIMIT_PER_MINUTE", 60, minimum=0)


def trust_proxy_headers() -> bool:
    """Read the client IP from X-Forwarded-For.

    Only enable this behind a proxy you control. If the service is reachable
    directly, a caller can forge the header and defeat the rate limiter.
    """
    return _bool("TRUST_PROXY_HEADERS", False)


# --- platforms and browser ----------------------------------------------

def platforms_raw() -> str:
    """Comma-separated platform IDs to enable. Empty means all of them."""
    return _str("PLATFORMS")


def chrome_path() -> str:
    return _str("CHROME_PATH")


def headless_max_concurrent() -> int:
    return _int("HEADLESS_MAX_CONCURRENT", 4)


def headless_timeout_sec() -> int:
    return _int("HEADLESS_TIMEOUT_SEC", 45)


def headless_wait_ms() -> int:
    return _int("HEADLESS_WAIT_MS", 6000)


def youtube_api_key() -> str:
    return _str("YOUTUBE_API_KEY")


# --- startup ------------------------------------------------------------

def validate() -> list[str]:
    """Check the configuration. Returns warnings; raises on fatal problems.

    The distinction matters operationally: a warning belongs in the log for the
    operator to weigh, while a fatal problem means this process should never
    accept traffic in the first place.
    """
    warnings: list[str] = []

    if is_production():
        if not access_token():
            raise ConfigError(
                "ACCESS_TOKEN must be set when ENV=production. Every request to "
                "this service drives a browser and reaches out to a social "
                "platform; left open, anyone can generate that traffic from your "
                "IP address and the resulting block lands on you.")
        if "*" in cors_origins():
            raise ConfigError(
                "CORS_ORIGINS must not be '*' while ACCESS_TOKEN is set — that "
                "combination exposes the token to every site a browser visits.")
        if rate_limit_per_minute() == 0:
            warnings.append(
                "RATE_LIMIT_PER_MINUTE is 0, so rate limiting is disabled. Only "
                "do this when a proxy in front of the service enforces its own.")
    else:
        if not access_token():
            warnings.append(
                "ACCESS_TOKEN is not set, so the API is open. That is fine "
                "locally, but it must be set before this is publicly reachable.")

    from . import browser
    if not browser.available():
        warnings.append(
            "Neither Chrome nor Edge was found, so Instagram, Facebook and "
            "LinkedIn image extraction will fail. Set CHROME_PATH, or restrict "
            "the service with PLATFORMS=x,youtube.")

    return warnings


def describe() -> dict:
    """The effective configuration, with no secret values in it.

    Logged once at startup: the first question about any misbehaving deployment
    is what it is actually configured with.
    """
    return {
        "env": env(),
        "version": APP_VERSION,
        "access_token_set": bool(access_token()),
        "cors_origins": cors_origins(),
        "rate_limit_per_minute": rate_limit_per_minute(),
        "trust_proxy_headers": trust_proxy_headers(),
        "platforms": platforms_raw() or "all",
        "headless_max_concurrent": headless_max_concurrent(),
        "headless_timeout_sec": headless_timeout_sec(),
        "youtube_api_key_set": bool(youtube_api_key()),
        "log_level": log_level(),
        "json_logs": json_logs(),
    }
