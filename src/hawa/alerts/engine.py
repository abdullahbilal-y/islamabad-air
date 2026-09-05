"""Threshold evaluation and alert dispatch.

Design notes worth knowing before you change this:

**Alerts are deduplicated by construction.** Every alert has a ``dedup_key``
built from (subscription, kind, sector, the period it refers to). The scheduler
runs every 30 minutes and PMD publishes once a day, so without that key every
subscriber would get the same "high pollen in H-8" message 48 times. The unique
index means re-evaluating is free and safe -- which in turn means we can
evaluate aggressively after every ingest without thinking about it.

**Evaluation reads the database once.** Not once per subscriber. With a few
hundred subscribers the per-subscriber query pattern is what makes a 200 ms job
into a 30 second one, and it is invisible in testing with three rows.

**Sending is separated from deciding.** ``evaluate`` writes pending alert rows
and returns; ``dispatch_pending`` sends them. A delivery failure therefore
leaves a retryable row rather than losing the alert, and a broken Telegram token
cannot stop threshold evaluation from recording what happened.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from hawa import settings_store
from hawa.models import AirReading, AlertEvent, PollenReading, Subscription, utcnow

log = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5


@dataclass(slots=True)
class Trigger:
    kind: str  # pollen | air
    sector: str | None
    label: str
    value: float
    threshold: float
    period: str  # the date/hour the reading belongs to, for dedup


def _csv_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    items = {part.strip() for part in value.split(",") if part.strip()}
    return items or None


def band_for(total: float, thresholds: dict[str, float] | None = None) -> str:
    """Name the severity band a total pollen count falls in."""
    bands = thresholds or settings_store.get("pollen_thresholds")
    if total >= bands.get("very_high", float("inf")):
        return "very_high"
    if total >= bands.get("high", float("inf")):
        return "high"
    if total >= bands.get("moderate", float("inf")):
        return "moderate"
    if total >= bands.get("low", float("inf")):
        return "low"
    return "absent"


def pollen_totals_by_sector(session: Session, observed_date: date) -> dict[str, float]:
    """Sum each sector's pollen types for a day. One query, all sectors."""
    rows = session.exec(
        select(PollenReading).where(
            PollenReading.observed_date == observed_date,
            PollenReading.value.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.sector] += float(row.value or 0)
    return dict(totals)


def pollen_peaks_by_sector(session: Session, observed_date: date) -> dict[str, tuple[str, float]]:
    """The single largest contributor per sector, so alerts can name it."""
    rows = session.exec(
        select(PollenReading).where(
            PollenReading.observed_date == observed_date,
            PollenReading.value.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    peaks: dict[str, tuple[str, float]] = {}
    for row in rows:
        value = float(row.value or 0)
        current = peaks.get(row.sector)
        if current is None or value > current[1]:
            peaks[row.sector] = (row.pollen_type, value)
    return peaks


def latest_pm25(session: Session, within_hours: int = 3) -> list[AirReading]:
    """Most recent PM2.5 reading per sensor, in one pass."""
    cutoff = utcnow() - timedelta(hours=within_hours)
    rows = session.exec(
        select(AirReading)
        .where(AirReading.metric == "pm25", AirReading.observed_at >= cutoff)
        .order_by(col(AirReading.observed_at).desc())
    ).all()
    seen: dict[tuple[str, str], AirReading] = {}
    for row in rows:
        key = (row.source, row.sensor_id)
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def evaluate(session: Session, observed_date: date | None = None) -> list[AlertEvent]:
    """Compare the latest data against thresholds and record pending alerts.

    Returns only the alerts newly created by this call. Alerts already recorded
    for the same subscription/sector/period are skipped by the dedup key, so
    calling this after every ingest is safe.
    """
    if not settings_store.get("alerts_enabled"):
        return []

    from hawa.parsers.pmd import today_pkt

    observed_date = observed_date or today_pkt()

    subscriptions = list(
        session.exec(select(Subscription).where(Subscription.active == True))  # noqa: E712
    )
    if not subscriptions:
        return []

    # Read the world once, then evaluate every subscriber against it in memory.
    totals = pollen_totals_by_sector(session, observed_date)
    peaks = pollen_peaks_by_sector(session, observed_date)
    pm_readings = latest_pm25(session)

    default_pollen = float(settings_store.get("pollen_alert_threshold"))
    default_pm25 = float(settings_store.get("pm25_alert_threshold"))

    created_ids: list[int] = []

    for sub in subscriptions:
        assert sub.id is not None  # persisted rows always have one
        sector_filter = _csv_set(sub.sectors)
        type_filter = _csv_set(sub.pollen_types)
        pollen_threshold = float(sub.pollen_threshold or default_pollen)
        pm25_threshold = float(sub.pm25_threshold or default_pm25)

        triggers: list[Trigger] = []

        for sector, total in totals.items():
            if sector_filter and sector not in sector_filter:
                continue
            peak_type, peak_value = peaks.get(sector, ("pollen", total))
            if type_filter and peak_type not in type_filter:
                continue
            if total < pollen_threshold:
                continue
            triggers.append(
                Trigger(
                    kind="pollen",
                    sector=sector,
                    label=(
                        f"Pollen {int(total):,} grains/m3 in {sector} "
                        f"({band_for(total).replace('_', ' ')}); "
                        f"highest is {peak_type} at {int(peak_value):,}"
                    ),
                    value=total,
                    threshold=pollen_threshold,
                    period=observed_date.isoformat(),
                )
            )

        for reading in pm_readings:
            if reading.value is None or reading.value < pm25_threshold:
                continue
            if sector_filter and reading.sector and reading.sector not in sector_filter:
                continue
            # Dedup per hour: PM2.5 updates continuously, and one alert per
            # sensor per hour is the most anyone wants during a smog episode.
            period = reading.observed_at.strftime("%Y-%m-%dT%H")
            name = reading.sensor_name or reading.sensor_id
            triggers.append(
                Trigger(
                    kind="air",
                    sector=reading.sector,
                    label=(
                        f"PM2.5 {reading.value:.0f} ug/m3 at {name} "
                        f"(threshold {pm25_threshold:.0f})"
                    ),
                    value=reading.value,
                    threshold=pm25_threshold,
                    period=period,
                )
            )

        for trigger in triggers:
            dedup_key = "|".join(
                [str(sub.id), trigger.kind, trigger.sector or "-", trigger.period]
            )
            event = AlertEvent(
                subscription_id=sub.id,
                dedup_key=dedup_key,
                kind=trigger.kind,
                sector=trigger.sector,
                trigger=trigger.label,
                value=trigger.value,
                threshold=trigger.threshold,
            )
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                # Already alerted for this subscriber/sector/period. Expected.
                session.rollback()
                continue
            assert event.id is not None  # set by the commit above
            created_ids.append(event.id)

    if not created_ids:
        return []
    # Each commit above expires the objects from earlier iterations, so re-load
    # them in one query. Returning expired instances would blow up the moment
    # the caller's session closed.
    return list(
        session.exec(select(AlertEvent).where(col(AlertEvent.id).in_(created_ids)))
    )


def dispatch_pending(session: Session, limit: int = 200) -> tuple[int, int]:
    """Send pending alerts. Returns ``(delivered, failed)``.

    A send that raises leaves the row ``pending`` with an incremented attempt
    count, so the next run retries it. After ``MAX_DELIVERY_ATTEMPTS`` the row
    is marked ``failed`` and left alone -- visible, but no longer retried
    forever against a target that is clearly gone.
    """
    from hawa.alerts.channels import send_alert

    pending = list(
        session.exec(
            select(AlertEvent)
            .where(AlertEvent.status == "pending")
            .order_by(col(AlertEvent.created_at))
            .limit(limit)
        )
    )
    if not pending:
        return 0, 0

    # Load every referenced subscription in one query, not one per alert.
    sub_ids = {event.subscription_id for event in pending}
    subs = {
        sub.id: sub
        for sub in session.exec(select(Subscription).where(col(Subscription.id).in_(sub_ids)))
    }

    delivered = failed = 0
    for event in pending:
        sub = subs.get(event.subscription_id)
        event.attempts += 1
        if sub is None or not sub.active:
            event.status = "failed"
            event.last_error = "subscription missing or inactive"
            failed += 1
            session.add(event)
            continue
        try:
            send_alert(sub, event)
        except Exception as exc:
            event.last_error = f"{type(exc).__name__}: {exc}"
            if event.attempts >= MAX_DELIVERY_ATTEMPTS:
                event.status = "failed"
                failed += 1
                log.error("giving up on alert %s after %s attempts", event.id, event.attempts)
            else:
                log.warning(
                    "alert %s delivery failed (attempt %s): %s", event.id, event.attempts, exc
                )
            session.add(event)
            continue

        event.status = "delivered"
        event.delivered_at = utcnow()
        event.last_error = None
        delivered += 1
        session.add(event)

    session.commit()
    return delivered, failed
