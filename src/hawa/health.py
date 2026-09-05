"""Outcome-based health.

A "connected" flag proves nothing. The PMD page can return a cheerful 200 OK
for months after a redesign while our parser silently extracts zero rows, and
if health only watched HTTP status nobody would notice until somebody asked
why the API had no data since March.

So health here is measured on the *outcome we actually depend on*: did a fetch
turn into rows? Two independent signals, both required to be green:

``success_ratio``
    Over a rolling window, how many attempts produced rows. Many attempts and
    near-zero successes means broken, whatever the status codes say.

``freshness``
    The newest reading we hold. Every fetch can succeed while the upstream
    itself quietly stops publishing -- that is a different failure and needs
    its own signal.

Deliberately, nothing here auto-restarts anything. A government page changing
its markup is not fixed by a restart; being told about it is the whole point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from hawa.config import get_config
from hawa.models import AirReading, FetchOutcome, PollenReading, utcnow


@dataclass(slots=True)
class SourceHealth:
    source: str
    status: str  # ok | degraded | unhealthy | unknown
    attempts: int
    successes: int
    success_ratio: float | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_stage: str | None
    last_failure_detail: str | None
    newest_observation: datetime | None
    age_hours: float | None
    reasons: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("last_success_at", "last_failure_at", "newest_observation"):
            value = data[key]
            data[key] = value.isoformat() if isinstance(value, datetime) else None
        return data


def record_outcome(
    session: Session,
    source: str,
    *,
    ok: bool,
    rows: int = 0,
    failure_stage: str | None = None,
    detail: str | None = None,
) -> FetchOutcome:
    """Log one attempt at the real work. Called on every path, success or not."""
    outcome = FetchOutcome(
        source=source,
        ok=ok,
        rows=rows,
        failure_stage=None if ok else (failure_stage or "unknown"),
        detail=detail[:4000] if detail else None,
    )
    session.add(outcome)
    return outcome


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as the UTC we stored."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _newest_observation(session: Session, source: str) -> datetime | None:
    newest_air = session.exec(
        select(col(AirReading.observed_at))
        .where(AirReading.source == source)
        .order_by(col(AirReading.observed_at).desc())
        .limit(1)
    ).first()
    newest_pollen_date = session.exec(
        select(col(PollenReading.observed_date))
        .where(PollenReading.source == source)
        .order_by(col(PollenReading.observed_date).desc())
        .limit(1)
    ).first()

    candidates = [_as_aware(newest_air)]
    if newest_pollen_date is not None:
        # Pollen is a daily figure; treat it as valid until the end of its day
        # so a same-day reading is never reported as hours stale.
        candidates.append(
            datetime.combine(newest_pollen_date, datetime.max.time(), tzinfo=timezone.utc)
        )
    real = [c for c in candidates if c is not None]
    return max(real) if real else None


def source_health(session: Session, source: str) -> SourceHealth:
    config = get_config()
    since = utcnow() - timedelta(minutes=config.health_window_minutes)

    outcomes = list(
        session.exec(
            select(FetchOutcome)
            .where(FetchOutcome.source == source, FetchOutcome.at >= since)
            .order_by(col(FetchOutcome.at).desc())
        ).all()
    )
    attempts = len(outcomes)
    successes = sum(1 for o in outcomes if o.ok)
    ratio = (successes / attempts) if attempts else None

    last_success = next((o for o in outcomes if o.ok), None)
    last_failure = next((o for o in outcomes if not o.ok), None)
    newest = _newest_observation(session, source)
    age_hours = ((utcnow() - newest).total_seconds() / 3600) if newest else None

    reasons: list[str] = []
    status = "ok"

    if attempts == 0:
        status = "unknown"
        reasons.append(f"no fetch attempts in the last {config.health_window_minutes} min")
    elif attempts >= config.health_min_attempts and (ratio or 0) < config.health_min_success_ratio:
        status = "unhealthy"
        reasons.append(
            f"{successes}/{attempts} attempts produced rows "
            f"(below {config.health_min_success_ratio:.0%})"
        )
    elif last_failure is not None and successes == 0:
        status = "unhealthy"
        reasons.append("every attempt in the window failed")
    elif last_failure is not None:
        status = "degraded"
        reasons.append(f"recent failure at {last_failure.failure_stage} stage")

    if age_hours is not None and age_hours > config.stale_after_hours:
        # Stale data outranks a green fetch ratio: fetching a page that has not
        # been updated in days is a success at the wrong thing.
        reasons.append(
            f"newest observation is {age_hours:.1f}h old "
            f"(stale after {config.stale_after_hours}h)"
        )
        status = "unhealthy" if status in {"ok", "unknown"} else status
    elif newest is None and status == "ok":
        status = "degraded"
        reasons.append("no observations stored yet")

    return SourceHealth(
        source=source,
        status=status,
        attempts=attempts,
        successes=successes,
        success_ratio=ratio,
        last_success_at=_as_aware(last_success.at) if last_success else None,
        last_failure_at=_as_aware(last_failure.at) if last_failure else None,
        last_failure_stage=last_failure.failure_stage if last_failure else None,
        last_failure_detail=last_failure.detail if last_failure else None,
        newest_observation=newest,
        age_hours=age_hours,
        reasons=reasons,
    )


def overall_health(session: Session, sources: list[str]) -> dict:
    """Aggregate for ``/healthz``. Unhealthy sources make the endpoint fail."""
    per_source = [source_health(session, s) for s in sources]
    worst = "ok"
    for health in per_source:
        if health.status == "unhealthy":
            worst = "unhealthy"
            break
        if health.status in {"degraded", "unknown"} and worst == "ok":
            worst = "degraded"
    return {
        "status": worst,
        "checked_at": utcnow().isoformat(),
        "sources": [h.to_dict() for h in per_source],
    }
