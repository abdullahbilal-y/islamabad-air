"""Database schema.

Two rules shape this schema and both come from hard-won lessons:

1. **Ingestion is sacred.** :class:`RawSnapshot` stores the exact bytes we
   fetched *before* anything tries to parse them. A parser bug or an upstream
   redesign can then be fixed and replayed against stored snapshots
   (``hawa reparse``) instead of losing a day of data forever.

2. **Every reading is idempotent on a natural key.** Re-running an ingest,
   backfilling, or replaying a snapshot can never create duplicates, which is
   what makes aggressive self-healing safe.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawSnapshot(SQLModel, table=True):
    """Exactly what an upstream returned, stored before parsing."""

    __tablename__ = "raw_snapshot"
    __table_args__ = (
        # The same body fetched twice in a day is one snapshot, not two.
        UniqueConstraint("source", "content_sha256", "fetched_date", name="uq_snapshot_identity"),
        Index("ix_snapshot_source_fetched", "source", "fetched_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    url: str
    status_code: int
    fetched_at: datetime = Field(default_factory=utcnow)
    fetched_date: date = Field(default_factory=lambda: utcnow().date())
    content_sha256: str
    body: str = Field(sa_column=Column(Text))

    # Filled in by the parse stage. A snapshot with parse_ok=False is a
    # replay candidate, never a discarded fetch.
    parse_ok: bool = Field(default=False)
    parser_strategy: str | None = None
    parse_error: str | None = Field(default=None, sa_column=Column(Text))
    rows_extracted: int = Field(default=0)


class PollenReading(SQLModel, table=True):
    """One pollen type, in one sector, on one day.

    ``value_raw`` is stored verbatim. We never overwrite a raw read with a
    cleaned-up derivation -- if ``value`` could not be parsed we keep the raw
    string and flag it, so a human can check it against the source page.
    """

    __tablename__ = "pollen_reading"
    __table_args__ = (
        UniqueConstraint(
            "source", "observed_date", "sector", "pollen_type", name="uq_pollen_observation"
        ),
        Index("ix_pollen_date_sector", "observed_date", "sector"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    observed_date: date = Field(index=True)
    sector: str = Field(index=True)
    pollen_type: str = Field(index=True)

    value_raw: str | None = None
    value: int | None = None
    unit: str = "grains/m3"

    #: Severity word the upstream itself printed ("Absent"/"Low"/...). Kept
    #: verbatim and separate from our own computed band, so a change in their
    #: banding never silently rewrites history.
    source_category: str | None = None
    needs_review: bool = Field(default=False)
    review_reason: str | None = None

    snapshot_id: int | None = Field(default=None, foreign_key="raw_snapshot.id")
    ingested_at: datetime = Field(default_factory=utcnow)


class AirReading(SQLModel, table=True):
    """One air-quality metric from one sensor at one instant."""

    __tablename__ = "air_reading"
    __table_args__ = (
        UniqueConstraint(
            "source", "sensor_id", "metric", "observed_at", name="uq_air_observation"
        ),
        Index("ix_air_observed", "observed_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    sensor_id: str = Field(index=True)
    sensor_name: str | None = None
    sector: str | None = Field(default=None, index=True)
    latitude: float | None = None
    longitude: float | None = None

    metric: str = Field(index=True)  # pm25 | pm10 | aqi | o3 | no2 ...
    value: float | None = None
    value_raw: str | None = None
    unit: str | None = None

    observed_at: datetime = Field(index=True)
    snapshot_id: int | None = Field(default=None, foreign_key="raw_snapshot.id")
    ingested_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    """Runtime-swappable config: thresholds, source URLs, enable toggles.

    Read through :mod:`hawa.settings_store`, which caches with a TTL and keeps
    the last known-good value so a transient DB hiccup cannot blank out config.
    """

    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value_json: str = Field(sa_column=Column(Text))
    description: str | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class Subscription(SQLModel, table=True):
    """Somebody who wants to be told when a threshold is crossed."""

    __tablename__ = "subscription"
    __table_args__ = (UniqueConstraint("channel", "target", name="uq_subscription_target"),)

    id: int | None = Field(default=None, primary_key=True)
    channel: str  # telegram | webhook
    target: str  # chat id, or an https:// URL
    label: str | None = None

    #: Comma-separated sector filter, empty/None means "all sectors".
    sectors: str | None = None
    #: Comma-separated pollen types, empty/None means "any".
    pollen_types: str | None = None

    #: Per-subscriber overrides; falls back to the global thresholds setting.
    pollen_threshold: int | None = None
    pm25_threshold: float | None = None

    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


class AlertEvent(SQLModel, table=True):
    """One alert we decided to send. ``dedup_key`` makes re-evaluation safe."""

    __tablename__ = "alert_event"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_alert_dedup"),)

    id: int | None = Field(default=None, primary_key=True)
    subscription_id: int = Field(foreign_key="subscription.id", index=True)
    dedup_key: str = Field(index=True)

    kind: str  # pollen | air
    sector: str | None = None
    trigger: str  # human-readable "Paper Mulberry 46132 in H-8"
    value: float | None = None
    threshold: float | None = None

    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None
    #: pending | delivered | failed -- a failed send stays a row so it can be retried.
    status: str = Field(default="pending", index=True)
    attempts: int = Field(default=0)
    last_error: str | None = Field(default=None, sa_column=Column(Text))


class FetchOutcome(SQLModel, table=True):
    """One attempt at doing the *real work* for a source, and whether it worked.

    Health is measured on outcomes, never on "did the HTTP client connect".
    A 200 OK whose body no longer parses is a failure here -- that is the exact
    shape of an upstream redesign, and it is invisible if you only watch status
    codes.
    """

    __tablename__ = "fetch_outcome"
    __table_args__ = (Index("ix_outcome_source_at", "source", "at"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    ok: bool
    #: network | http | parse | none  -- which stage failed.
    failure_stage: str | None = None
    rows: int = Field(default=0)
    detail: str | None = Field(default=None, sa_column=Column(Text))
