"""Process-level configuration.

Only bootstrap and secret values live here. Anything that changes more often
than the code (thresholds, source URLs, poll interval) lives in the ``settings``
table instead -- see :mod:`hawa.settings_store` and brain/landmines.md #6.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HAWA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Storage. SQLite by default so `hawa serve` works with zero setup.
    database_url: str = "sqlite:///./hawa.db"

    # Secrets / credentials. Absent key => that source is skipped, not fatal.
    telegram_bot_token: str | None = None
    purpleair_api_key: str | None = None
    openaq_api_key: str | None = None

    # Scheduler
    enable_scheduler: bool = True
    poll_interval_minutes: int = 30

    # Networking
    http_timeout_seconds: float = 30.0
    user_agent: str = (
        "hawa/0.1 (+https://github.com/abdullahbilal-y/islamabad-air) "
        "open-data client; contact via GitHub issues"
    )

    # Health: how far back the outcome-health window looks, and how many
    # attempts must accumulate before a low success rate counts as unhealthy.
    health_window_minutes: int = 180
    health_min_attempts: int = 3
    health_min_success_ratio: float = 0.34

    # A source whose newest reading is older than this is "stale" even if
    # every fetch succeeded -- the upstream stopped publishing.
    stale_after_hours: int = 36

    cors_allow_origins: str = "*"


@lru_cache
def get_config() -> Config:
    return Config()
