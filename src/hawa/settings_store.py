"""Runtime config that lives in a DB row, not in code.

Thresholds and source URLs change faster than releases do: PMD moves a page,
a threshold turns out to be too noisy in March. Those live in the ``setting``
table and take effect on the next cache refresh -- no redeploy.

Two guarantees matter here:

* a read failure returns the **last known-good** value rather than blanking the
  config out (an empty read is not the same as "nothing is configured");
* every key has a code-level default, so a fresh database is immediately usable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from sqlmodel import select

from hawa.db import session_scope
from hawa.models import Setting

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0

#: Every runtime-tunable value, with its default and a note for the admin UI.
DEFAULTS: dict[str, tuple[Any, str]] = {
    "pmd_pollen_url": (
        "https://weather.gov.pk/rnd/pollen-data",
        "PMD's Islamabad pollen page. If PMD moves it, change this row -- not the code.",
    ),
    "pmd_pollen_ajax_url": (
        "https://weather.gov.pk/rnd/pollen-data/ajax-data",
        "PMD's month-scoped JSON endpoint. Tried as a secondary strategy.",
    ),
    "sectors": (
        ["H-8", "E-8", "G-6", "F-10"],
        "Islamabad sectors PMD publishes pollen counts for.",
    ),
    "pollen_thresholds": (
        # grains/m3 per 24h. Bands follow PMD's own published wording.
        {"low": 1000, "moderate": 5000, "high": 15000, "very_high": 30000},
        "Total-pollen bands in grains/m3. 'high' is the default alert trigger.",
    ),
    "pollen_alert_threshold": (
        15000,
        "Total daily pollen count in a sector that triggers an alert.",
    ),
    "pm25_alert_threshold": (
        55.0,
        "PM2.5 ug/m3 that triggers an air-quality alert (US AQI 'Unhealthy' boundary).",
    ),
    "enabled_sources": (
        ["pmd_pollen"],
        "Which source ids the scheduler polls. Add 'purpleair'/'openaq' once keyed.",
    ),
    "alerts_enabled": (True, "Master switch for outbound notifications."),
}

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_at: float = 0.0


def _load_from_db() -> dict[str, Any]:
    # Read the columns inside the session. Returning ORM objects and touching
    # their attributes afterwards raises DetachedInstanceError, which the
    # caller's broad except would quietly turn into "keeping last known-good"
    # -- a settings write that appears to succeed and never takes effect.
    with session_scope() as session:
        rows = [(row.key, row.value_json) for row in session.exec(select(Setting)).all()]

    loaded: dict[str, Any] = {}
    for key, value_json in rows:
        try:
            loaded[key] = json.loads(value_json)
        except json.JSONDecodeError:
            # A malformed row must not poison the whole config; fall back to
            # the code default for that one key.
            log.warning("setting %r holds invalid JSON; using default", key)
    return loaded


def refresh(force: bool = False) -> dict[str, Any]:
    global _cache, _cache_at
    with _lock:
        fresh_enough = (time.monotonic() - _cache_at) < CACHE_TTL_SECONDS
        if _cache and fresh_enough and not force:
            return _cache
        try:
            db_values = _load_from_db()
        except Exception as exc:  # pragma: no cover - defensive
            # Keep last known-good rather than falling back to bare defaults.
            log.warning("settings refresh failed (%s); keeping last known-good", exc)
            if _cache:
                return _cache
            db_values = {}
        merged = {key: default for key, (default, _) in DEFAULTS.items()}
        merged.update(db_values)
        _cache = merged
        _cache_at = time.monotonic()
        return _cache


def get(key: str) -> Any:
    values = refresh()
    if key in values:
        return values[key]
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    raise KeyError(f"unknown setting: {key}")


def set_value(key: str, value: Any, description: str | None = None) -> None:
    from hawa.models import utcnow

    with session_scope() as session:
        row = session.get(Setting, key)
        if row is None:
            row = Setting(
                key=key,
                value_json=json.dumps(value),
                description=description or (DEFAULTS.get(key) or (None, None))[1],
            )
        else:
            row.value_json = json.dumps(value)
            row.updated_at = utcnow()
            if description:
                row.description = description
        session.add(row)
    refresh(force=True)


def all_settings() -> dict[str, Any]:
    return dict(refresh())


def reset_cache() -> None:
    """Test hook."""
    global _cache, _cache_at
    with _lock:
        _cache = {}
        _cache_at = 0.0
