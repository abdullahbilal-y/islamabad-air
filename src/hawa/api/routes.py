"""Public REST API.

Everything here is read-only and unauthenticated except subscription
management, which is deliberately kept simple for v0 -- see the note on
``POST /v1/subscriptions``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, select

from hawa import settings_store
from hawa.alerts.engine import band_for
from hawa.api.schemas import (
    AirReadingOut,
    AlertEventOut,
    PollenDayOut,
    PollenReadingOut,
    SectorSummaryOut,
    SourceOut,
    SubscriptionIn,
    SubscriptionOut,
)
from hawa.db import get_session
from hawa.health import overall_health, source_health
from hawa.models import AirReading, AlertEvent, PollenReading, Subscription
from hawa.parsers.pmd import today_pkt
from hawa.sources import all_sources

router = APIRouter(prefix="/v1", tags=["data"])

ATTRIBUTION = (
    "Pollen counts: Pakistan Meteorological Department. "
    "Air quality: PurpleAir community sensors and OpenAQ. "
    "Served by Hawa, an independent open-source project -- not an official PMD service."
)


def _summarise(readings: list[PollenReading], observed_date: date) -> list[SectorSummaryOut]:
    by_sector: dict[str, list[PollenReading]] = defaultdict(list)
    for reading in readings:
        by_sector[reading.sector].append(reading)

    summaries: list[SectorSummaryOut] = []
    for sector, rows in sorted(by_sector.items()):
        numeric = [r for r in rows if r.value is not None]
        total = sum(int(r.value or 0) for r in numeric)
        top = max(numeric, key=lambda r: r.value or 0, default=None)
        summaries.append(
            SectorSummaryOut(
                sector=sector,
                observed_date=observed_date,
                total=total,
                band=band_for(total),
                top_type=top.pollen_type if top else None,
                top_value=int(top.value) if top and top.value is not None else None,
                types_reported=len(numeric),
            )
        )
    return summaries


@router.get(
    "/pollen/latest",
    response_model=PollenDayOut,
    summary="Most recent day of sector-wise pollen counts",
)
def pollen_latest(session: Session = Depends(get_session)) -> PollenDayOut:
    """The newest day we hold, which is usually today or yesterday in PKT.

    We return the newest day we *actually have*, not "today with no rows" --
    an empty response would be indistinguishable from a genuine zero-pollen day.
    """
    newest = session.exec(
        select(col(PollenReading.observed_date)).order_by(
            col(PollenReading.observed_date).desc()
        )
    ).first()
    if newest is None:
        raise HTTPException(
            status_code=503,
            detail="No pollen data stored yet. Run 'hawa ingest' or wait for the scheduler.",
        )
    return _pollen_for_date(session, newest)


@router.get(
    "/pollen/{observed_date}",
    response_model=PollenDayOut,
    summary="Sector-wise pollen counts for a specific date",
)
def pollen_for_date(
    observed_date: date, session: Session = Depends(get_session)
) -> PollenDayOut:
    return _pollen_for_date(session, observed_date)


def _pollen_for_date(session: Session, observed_date: date) -> PollenDayOut:
    readings = list(
        session.exec(
            select(PollenReading)
            .where(PollenReading.observed_date == observed_date)
            .order_by(col(PollenReading.sector), col(PollenReading.pollen_type))
        )
    )
    if not readings:
        raise HTTPException(
            status_code=404, detail=f"No pollen readings stored for {observed_date.isoformat()}"
        )
    return PollenDayOut(
        observed_date=observed_date,
        sectors=_summarise(readings, observed_date),
        readings=[PollenReadingOut.model_validate(r) for r in readings],
        attribution=ATTRIBUTION,
    )


@router.get(
    "/pollen",
    response_model=list[PollenReadingOut],
    summary="Query pollen readings over a date range",
)
def pollen_range(
    session: Session = Depends(get_session),
    start: date | None = Query(default=None, description="Inclusive. Defaults to 30 days ago."),
    end: date | None = Query(default=None, description="Inclusive. Defaults to today (PKT)."),
    sector: str | None = Query(default=None, description="e.g. H-8"),
    pollen_type: str | None = Query(default=None, description="e.g. 'Paper Mulberry'"),
    limit: int = Query(default=1000, le=10_000),
) -> list[PollenReadingOut]:
    end = end or today_pkt()
    start = start or (end - timedelta(days=30))
    if start > end:
        raise HTTPException(status_code=400, detail="start must not be after end")

    query = select(PollenReading).where(
        PollenReading.observed_date >= start, PollenReading.observed_date <= end
    )
    if sector:
        query = query.where(PollenReading.sector == sector.upper())
    if pollen_type:
        query = query.where(PollenReading.pollen_type == pollen_type)

    rows = session.exec(
        query.order_by(
            col(PollenReading.observed_date).desc(), col(PollenReading.sector)
        ).limit(limit)
    ).all()
    return [PollenReadingOut.model_validate(r) for r in rows]


@router.get(
    "/pollen/sectors/summary",
    response_model=list[SectorSummaryOut],
    summary="Per-sector totals and severity band for one day",
)
def sector_summary(
    session: Session = Depends(get_session),
    observed_date: date | None = Query(default=None, description="Defaults to the newest day."),
) -> list[SectorSummaryOut]:
    if observed_date is None:
        observed_date = session.exec(
            select(col(PollenReading.observed_date)).order_by(
                col(PollenReading.observed_date).desc()
            )
        ).first()
        if observed_date is None:
            return []
    readings = list(
        session.exec(
            select(PollenReading).where(PollenReading.observed_date == observed_date)
        )
    )
    return _summarise(readings, observed_date)


@router.get("/air/latest", response_model=list[AirReadingOut], summary="Latest air readings")
def air_latest(
    session: Session = Depends(get_session),
    metric: str = Query(default="pm25"),
    within_hours: int = Query(default=6, ge=1, le=168),
) -> list[AirReadingOut]:
    from hawa.models import utcnow

    cutoff = utcnow() - timedelta(hours=within_hours)
    rows = session.exec(
        select(AirReading)
        .where(AirReading.metric == metric, AirReading.observed_at >= cutoff)
        .order_by(col(AirReading.observed_at).desc())
    ).all()

    newest: dict[tuple[str, str], AirReading] = {}
    for row in rows:
        newest.setdefault((row.source, row.sensor_id), row)
    return [AirReadingOut.model_validate(r) for r in newest.values()]


@router.get("/sources", response_model=list[SourceOut], summary="Data sources and their state")
def list_sources() -> list[SourceOut]:
    enabled = set(settings_store.get("enabled_sources") or [])
    out: list[SourceOut] = []
    for source in all_sources():
        available, reason = source.available()
        out.append(
            SourceOut(
                id=source.id,
                name=source.name,
                provides=sorted(source.provides),
                attribution=source.attribution,
                enabled=source.id in enabled,
                available=available,
                unavailable_reason=reason,
            )
        )
    return out


@router.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=201,
    summary="Subscribe to threshold alerts",
)
def create_subscription(
    payload: SubscriptionIn, session: Session = Depends(get_session)
) -> SubscriptionOut:
    """Register an alert target.

    v0 has no auth on this endpoint, which is fine for a local or trusted
    deployment and is NOT fine on the open internet -- anyone could point a
    webhook at a third party and make this service send traffic on their
    behalf. Put it behind auth or a rate limit before exposing it publicly;
    tracked in brain/landmines.md #7.
    """
    if payload.channel not in {"telegram", "webhook"}:
        raise HTTPException(status_code=400, detail="channel must be 'telegram' or 'webhook'")
    if payload.channel == "webhook" and not payload.target.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="webhook target must be an http(s) URL")

    existing = session.exec(
        select(Subscription).where(
            Subscription.channel == payload.channel, Subscription.target == payload.target
        )
    ).first()
    if existing is not None:
        # Re-subscribing reactivates rather than erroring -- it is what the
        # caller meant, and it makes the endpoint idempotent.
        existing.active = True
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return SubscriptionOut.model_validate(existing)

    sub = Subscription(**payload.model_dump())
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return SubscriptionOut.model_validate(sub)


@router.delete("/subscriptions/{subscription_id}", status_code=204, summary="Unsubscribe")
def delete_subscription(subscription_id: int, session: Session = Depends(get_session)) -> None:
    sub = session.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="no such subscription")
    sub.active = False
    session.add(sub)
    session.commit()


@router.get("/alerts", response_model=list[AlertEventOut], summary="Recent alert events")
def list_alerts(
    session: Session = Depends(get_session), limit: int = Query(default=50, le=500)
) -> list[AlertEventOut]:
    rows = session.exec(
        select(AlertEvent)
        .order_by(col(AlertEvent.created_at).desc())
        .limit(limit)
    ).all()
    return [AlertEventOut.model_validate(r) for r in rows]


@router.get("/health/sources", summary="Per-source outcome health")
def sources_health(session: Session = Depends(get_session)) -> dict:
    enabled = list(settings_store.get("enabled_sources") or [])
    return {"sources": [source_health(session, s).to_dict() for s in enabled]}


@router.get("/settings", summary="Current runtime settings")
def read_settings() -> dict:
    """Non-secret runtime config. Useful for debugging a deployment."""
    return settings_store.all_settings()


health_router = APIRouter(tags=["ops"])


@health_router.get(
    "/healthz", summary="Liveness and data-freshness check", response_model=None
)
def healthz(session: Session = Depends(get_session)) -> JSONResponse:
    """Returns 503 when a source is failing or its data has gone stale.

    This checks whether ingest is *working*, not whether the process is up.
    A green process serving month-old pollen counts is the failure this
    endpoint exists to catch.
    """
    enabled = list(settings_store.get("enabled_sources") or [])
    report = overall_health(session, enabled)
    status_code = 503 if report["status"] == "unhealthy" else 200
    return JSONResponse(status_code=status_code, content=report)
