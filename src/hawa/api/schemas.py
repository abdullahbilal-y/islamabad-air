"""Response models. These are the public contract -- change them carefully."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class PollenReadingOut(BaseModel):
    source: str
    observed_date: date
    sector: str
    pollen_type: str
    value: int | None = Field(
        default=None, description="Parsed count in grains/m3. Null if the cell was not a number."
    )
    value_raw: str | None = Field(
        default=None,
        description="Exactly what the source printed, kept verbatim so a doubtful "
        "reading can be checked against the original page.",
    )
    unit: str
    source_category: str | None = Field(
        default=None, description="Severity word the source itself used, if any."
    )
    needs_review: bool = Field(
        default=False, description="True when value_raw could not be parsed into a number."
    )

    model_config = {"from_attributes": True}


class SectorSummaryOut(BaseModel):
    sector: str
    observed_date: date
    total: int = Field(description="Sum of every pollen type reported for this sector.")
    band: str = Field(description="absent | low | moderate | high | very_high")
    top_type: str | None = None
    top_value: int | None = None
    types_reported: int


class PollenDayOut(BaseModel):
    observed_date: date
    sectors: list[SectorSummaryOut]
    readings: list[PollenReadingOut]
    attribution: str


class AirReadingOut(BaseModel):
    source: str
    sensor_id: str
    sensor_name: str | None = None
    sector: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    metric: str
    value: float | None = None
    unit: str | None = None
    observed_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionIn(BaseModel):
    channel: str = Field(description="telegram | webhook")
    target: str = Field(description="Telegram chat id, or an https:// URL for a webhook.")
    label: str | None = None
    sectors: str | None = Field(
        default=None, description="Comma-separated sectors, e.g. 'H-8,G-6'. Empty means all."
    )
    pollen_types: str | None = None
    pollen_threshold: int | None = None
    pm25_threshold: float | None = None


class SubscriptionOut(BaseModel):
    id: int
    channel: str
    target: str
    label: str | None = None
    sectors: str | None = None
    pollen_types: str | None = None
    pollen_threshold: int | None = None
    pm25_threshold: float | None = None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertEventOut(BaseModel):
    id: int
    kind: str
    sector: str | None = None
    trigger: str
    value: float | None = None
    threshold: float | None = None
    status: str
    created_at: datetime
    delivered_at: datetime | None = None

    model_config = {"from_attributes": True}


class SourceOut(BaseModel):
    id: str
    name: str
    provides: list[str]
    attribution: str
    enabled: bool
    available: bool
    unavailable_reason: str | None = None
