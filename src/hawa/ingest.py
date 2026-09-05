"""The ingest pipeline.

The ordering here is the whole point, so it is worth stating plainly:

1. **Fetch.** Network only.
2. **Persist the raw body, and commit.** Before a single line of parsing runs.
   Today's pollen count exists for one day; if our parser is broken we still
   want the bytes, because PMD will not re-publish them for us.
3. **Parse, in its own try/except.** A parse failure marks the snapshot and is
   recorded as an unhealthy outcome. It never rolls back step 2.
4. **Upsert observations** on their natural key, so re-running this function --
   after a crash, on a schedule, or via ``hawa reparse`` -- can never duplicate
   a row.

Because of (4), recovery is free: ``reparse_snapshots`` replays stored bodies
through the current parser and repairs days that a since-fixed bug mangled.
That is only safe because every write is idempotent, which is why the natural
keys in :mod:`hawa.models` matter more than they look.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from hawa import settings_store
from hawa.db import get_engine, session_scope
from hawa.health import record_outcome
from hawa.models import AirReading, PollenReading, RawSnapshot, utcnow
from hawa.sources import Source, all_sources, build_client, get_source
from hawa.sources.base import Fetched, FetchError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    source: str
    ok: bool
    stage: str  # fetched | parsed | stored | skipped
    snapshot_id: int | None = None
    strategy: str | None = None
    rows_parsed: int = 0
    rows_written: int = 0
    rows_duplicate: int = 0
    error: str | None = None
    skipped_reason: str | None = None

    def summary(self) -> str:
        if self.skipped_reason:
            return f"{self.source}: skipped ({self.skipped_reason})"
        if not self.ok:
            return f"{self.source}: FAILED at {self.stage} -- {self.error}"
        return (
            f"{self.source}: ok via {self.strategy} -- "
            f"{self.rows_written} new, {self.rows_duplicate} already held"
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def store_snapshot(session: Session, source_id: str, fetched: Fetched) -> RawSnapshot:
    """Persist the raw body, or return the existing row for identical content.

    Deduplicating on (source, content hash, date) means polling every 30 minutes
    does not accumulate 48 identical copies of an unchanged page per day, while
    still keeping one snapshot per distinct version.
    """
    digest = _sha256(fetched.body)
    fetched_date = fetched.fetched_at.date()

    existing = session.exec(
        select(RawSnapshot).where(
            RawSnapshot.source == source_id,
            RawSnapshot.content_sha256 == digest,
            RawSnapshot.fetched_date == fetched_date,
        )
    ).first()
    if existing is not None:
        return existing

    snapshot = RawSnapshot(
        source=source_id,
        url=fetched.url,
        status_code=fetched.status_code,
        fetched_at=fetched.fetched_at,
        fetched_date=fetched_date,
        content_sha256=digest,
        body=fetched.body,
    )
    session.add(snapshot)
    try:
        session.commit()
    except IntegrityError:
        # Another worker stored the same body between our check and our insert.
        session.rollback()
        existing = session.exec(
            select(RawSnapshot).where(
                RawSnapshot.source == source_id,
                RawSnapshot.content_sha256 == digest,
                RawSnapshot.fetched_date == fetched_date,
            )
        ).first()
        if existing is None:  # pragma: no cover - genuinely unexpected
            raise
        return existing
    session.refresh(snapshot)
    return snapshot


def _upsert_pollen(session: Session, source_id: str, obs, snapshot_id: int | None) -> bool:
    """Return True if a new row was written, False if we already held it."""
    existing = session.exec(
        select(PollenReading).where(
            PollenReading.source == source_id,
            PollenReading.observed_date == obs.observed_date,
            PollenReading.sector == obs.sector,
            PollenReading.pollen_type == obs.pollen_type,
        )
    ).first()

    if existing is not None:
        # A same-day correction from upstream is a legitimate update, but only
        # if the printed text actually differs -- otherwise leave history alone.
        if existing.value_raw != obs.value_raw:
            existing.value_raw = obs.value_raw
            existing.value = obs.value
            existing.source_category = obs.source_category
            existing.needs_review = obs.needs_review
            existing.review_reason = obs.review_reason
            existing.snapshot_id = snapshot_id
            existing.ingested_at = utcnow()
            session.add(existing)
        return False

    session.add(
        PollenReading(
            source=source_id,
            observed_date=obs.observed_date,
            sector=obs.sector,
            pollen_type=obs.pollen_type,
            value_raw=obs.value_raw,
            value=obs.value,
            unit=obs.unit,
            source_category=obs.source_category,
            needs_review=obs.needs_review,
            review_reason=obs.review_reason,
            snapshot_id=snapshot_id,
        )
    )
    return True


def _upsert_air(session: Session, source_id: str, obs, snapshot_id: int | None) -> bool:
    existing = session.exec(
        select(AirReading).where(
            AirReading.source == source_id,
            AirReading.sensor_id == obs.sensor_id,
            AirReading.metric == obs.metric,
            AirReading.observed_at == obs.observed_at,
        )
    ).first()
    if existing is not None:
        return False

    session.add(
        AirReading(
            source=source_id,
            sensor_id=obs.sensor_id,
            sensor_name=obs.sensor_name,
            sector=obs.sector,
            latitude=obs.latitude,
            longitude=obs.longitude,
            metric=obs.metric,
            value=obs.value,
            value_raw=obs.value_raw,
            unit=obs.unit,
            observed_at=obs.observed_at,
            snapshot_id=snapshot_id,
        )
    )
    return True


def store_parsed(
    session: Session, source_id: str, result, snapshot_id: int | None
) -> tuple[int, int]:
    """Idempotently write observations. Returns ``(written, duplicates)``."""
    written = duplicates = 0
    for obs in result.pollen:
        if _upsert_pollen(session, source_id, obs, snapshot_id):
            written += 1
        else:
            duplicates += 1
    for obs in result.air:
        if _upsert_air(session, source_id, obs, snapshot_id):
            written += 1
        else:
            duplicates += 1
    session.commit()
    return written, duplicates


def ingest_source(source: Source | str, *, client=None) -> IngestReport:
    """Run one source end to end. Never raises for an expected failure."""
    src = get_source(source) if isinstance(source, str) else source

    can_run, reason = src.available()
    if not can_run:
        log.info("skipping %s: %s", src.id, reason)
        return IngestReport(source=src.id, ok=True, stage="skipped", skipped_reason=reason)

    owns_client = client is None
    client = client or build_client()

    try:
        # --- 1. fetch -------------------------------------------------------
        try:
            fetched = src.fetch(client)
        except FetchError as exc:
            with session_scope() as session:
                record_outcome(
                    session, src.id, ok=False, failure_stage="network", detail=str(exc)
                )
            log.warning("%s fetch failed: %s", src.id, exc)
            return IngestReport(source=src.id, ok=False, stage="fetched", error=str(exc))
        except Exception as exc:
            with session_scope() as session:
                record_outcome(
                    session, src.id, ok=False, failure_stage="network", detail=repr(exc)
                )
            log.exception("%s fetch raised unexpectedly", src.id)
            return IngestReport(source=src.id, ok=False, stage="fetched", error=repr(exc))

        return ingest_fetched(src, fetched)
    finally:
        if owns_client:
            client.close()


def ingest_fetched(source: Source | str, fetched: Fetched) -> IngestReport:
    """Store and parse an already-fetched body.

    Split out from :func:`ingest_source` because the fetch and the processing
    have different constraints: PMD refuses requests from outside Pakistan, so
    the fetch may have to happen somewhere entirely different from where the
    parsing and publishing run. Everything after the fetch is pure local work
    and can happen anywhere -- including in CI, from a body someone else
    retrieved.

    The ordering guarantees are identical either way: raw body committed first,
    parse isolated, writes idempotent.
    """
    src = get_source(source) if isinstance(source, str) else source

    with Session(get_engine()) as session:
        # --- persist raw, and commit, before parsing -----------------------
        snapshot = store_snapshot(session, src.id, fetched)
        snapshot_id = snapshot.id

        # --- parse (isolated: a failure must not lose the snapshot) --------
        try:
            result = src.parse(fetched)
        except Exception as exc:
            snapshot.parse_ok = False
            snapshot.parse_error = f"{type(exc).__name__}: {exc}"
            session.add(snapshot)
            # A 200 OK we cannot parse is the signature of an upstream
            # redesign, so it counts against health just like a timeout.
            record_outcome(session, src.id, ok=False, failure_stage="parse", detail=str(exc))
            session.commit()
            log.error("%s parse failed (snapshot %s kept): %s", src.id, snapshot_id, exc)
            return IngestReport(
                source=src.id,
                ok=False,
                stage="parsed",
                snapshot_id=snapshot_id,
                error=str(exc),
            )

        # --- idempotent write ----------------------------------------------
        written, duplicates = store_parsed(session, src.id, result, snapshot_id)

        snapshot.parse_ok = True
        snapshot.parser_strategy = result.strategy
        snapshot.parse_error = None
        snapshot.rows_extracted = result.row_count
        session.add(snapshot)
        record_outcome(session, src.id, ok=True, rows=result.row_count)
        session.commit()

    return IngestReport(
        source=src.id,
        ok=True,
        stage="stored",
        snapshot_id=snapshot_id,
        strategy=result.strategy,
        rows_parsed=result.row_count,
        rows_written=written,
        rows_duplicate=duplicates,
    )


def ingest_enabled_sources() -> list[IngestReport]:
    """Poll every source listed in the ``enabled_sources`` setting."""
    enabled = set(settings_store.get("enabled_sources") or [])
    reports: list[IngestReport] = []
    with build_client() as client:
        for source in all_sources():
            if source.id not in enabled:
                continue
            reports.append(ingest_source(source, client=client))
    return reports


def reparse_snapshots(
    source_id: str | None = None,
    *,
    since: date | None = None,
    only_failed: bool = True,
) -> list[IngestReport]:
    """Replay stored bodies through the current parsers.

    This is the payoff for keeping raw snapshots: fix a parser, run this, and
    the days it previously mangled are repaired without re-fetching anything
    (which for a daily figure would be impossible anyway).
    """
    reports: list[IngestReport] = []
    with session_scope() as session:
        query = select(RawSnapshot)
        if source_id:
            query = query.where(RawSnapshot.source == source_id)
        if only_failed:
            query = query.where(RawSnapshot.parse_ok == False)  # noqa: E712
        if since:
            query = query.where(RawSnapshot.fetched_date >= since)
        snapshots = list(session.exec(query.order_by(col(RawSnapshot.fetched_at))).all())

        for snapshot in snapshots:
            try:
                src = get_source(snapshot.source)
            except KeyError as exc:
                reports.append(
                    IngestReport(
                        source=snapshot.source, ok=False, stage="parsed", error=str(exc)
                    )
                )
                continue

            fetched = Fetched(
                url=snapshot.url,
                status_code=snapshot.status_code,
                body=snapshot.body,
                fetched_at=snapshot.fetched_at,
            )
            try:
                result = src.parse(fetched)
            except Exception as exc:
                snapshot.parse_error = f"{type(exc).__name__}: {exc}"
                snapshot.parse_ok = False
                session.add(snapshot)
                reports.append(
                    IngestReport(
                        source=snapshot.source,
                        ok=False,
                        stage="parsed",
                        snapshot_id=snapshot.id,
                        error=str(exc),
                    )
                )
                continue

            written, duplicates = store_parsed(session, snapshot.source, result, snapshot.id)
            snapshot.parse_ok = True
            snapshot.parser_strategy = result.strategy
            snapshot.parse_error = None
            snapshot.rows_extracted = result.row_count
            session.add(snapshot)
            reports.append(
                IngestReport(
                    source=snapshot.source,
                    ok=True,
                    stage="stored",
                    snapshot_id=snapshot.id,
                    strategy=result.strategy,
                    rows_parsed=result.row_count,
                    rows_written=written,
                    rows_duplicate=duplicates,
                )
            )
    return reports
