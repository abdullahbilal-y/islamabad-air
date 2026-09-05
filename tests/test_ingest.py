"""Ingest pipeline: idempotency, raw-snapshot survival, and health accounting."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from hawa.db import get_engine
from hawa.ingest import ingest_source, reparse_snapshots
from hawa.models import FetchOutcome, PollenReading, RawSnapshot
from hawa.sources.base import Fetched, FetchError, ParseResult, Source


class FakeSource(Source):
    """A source we can make succeed, fail to fetch, or fail to parse on demand."""

    id = "pmd_pollen"  # reuse a registered id so reparse can find it
    name = "fake"
    provides = frozenset({"pollen"})

    def __init__(self, body: str, *, fetch_error: str | None = None):
        self.body = body
        self.fetch_error = fetch_error
        self.fetch_calls = 0

    def fetch(self, client) -> Fetched:
        self.fetch_calls += 1
        if self.fetch_error:
            raise FetchError(self.fetch_error)
        return Fetched(
            url="https://example.test/pollen",
            status_code=200,
            body=self.body,
            fetched_at=datetime(2026, 4, 1, 6, 0, tzinfo=timezone.utc),
        )

    def parse(self, fetched: Fetched) -> ParseResult:
        from hawa.parsers.pmd import parse_pmd_pollen
        from hawa.sources.base import PollenObservation

        parsed = parse_pmd_pollen(fetched.body)
        return ParseResult(
            pollen=[
                PollenObservation(
                    observed_date=parsed.observed_date,
                    sector=row.sector,
                    pollen_type=row.pollen_type,
                    value_raw=row.value_raw,
                    value=row.value,
                    source_category=row.source_category,
                    needs_review=row.needs_review,
                    review_reason=row.review_reason,
                )
                for row in parsed.rows
            ],
            strategy=parsed.strategy,
        )


GOOD_BODY = (
    'rows.push({ type: "Paper Mulberry", \'H-8\': "46132", category: "Very High" });'
    'rows.push({ type: "Pines", \'H-8\': "40", category: "Moderate" });'
    '<div id="lastUpdated">Last Updated: March 19, 2026</div>'
)


def _rows(model):
    with Session(get_engine()) as session:
        return list(session.exec(select(model)))


def test_ingest_writes_readings_and_a_snapshot(db):
    report = ingest_source(FakeSource(GOOD_BODY), client=object())

    assert report.ok and report.stage == "stored"
    assert report.rows_written == 2
    assert len(_rows(PollenReading)) == 2
    assert len(_rows(RawSnapshot)) == 1
    assert _rows(RawSnapshot)[0].parse_ok is True


def test_running_twice_writes_nothing_new(db):
    """Idempotency is what makes the scheduler, retries and reparse all safe."""
    ingest_source(FakeSource(GOOD_BODY), client=object())
    second = ingest_source(FakeSource(GOOD_BODY), client=object())

    assert second.rows_written == 0
    assert second.rows_duplicate == 2
    assert len(_rows(PollenReading)) == 2, "a re-poll must not duplicate the day"
    assert len(_rows(RawSnapshot)) == 1, "an unchanged body is one snapshot, not two"


def test_upstream_correction_updates_in_place(db):
    ingest_source(FakeSource(GOOD_BODY), client=object())
    corrected = GOOD_BODY.replace('"46132"', '"46200"')
    ingest_source(FakeSource(corrected), client=object())

    readings = [r for r in _rows(PollenReading) if r.pollen_type == "Paper Mulberry"]
    assert len(readings) == 1
    assert readings[0].value == 46200
    assert readings[0].value_raw == "46200"


def test_parse_failure_keeps_the_raw_body(db):
    """The point of the whole design: a broken parser must not lose the data."""
    report = ingest_source(FakeSource("<html>redesigned, unparseable</html>"), client=object())

    assert report.ok is False
    assert report.stage == "parsed"

    snapshots = _rows(RawSnapshot)
    assert len(snapshots) == 1, "the fetched body is kept even though parsing failed"
    assert snapshots[0].parse_ok is False
    assert snapshots[0].parse_error
    assert snapshots[0].body == "<html>redesigned, unparseable</html>"
    assert _rows(PollenReading) == []


def test_reparse_recovers_a_day_after_the_parser_is_fixed(db, monkeypatch):
    """Fix the parser, replay the snapshot, get the day back.

    This is the payoff for storing raw bodies. Yesterday's PMD page cannot be
    re-fetched, so without this the day would be gone permanently.
    """
    broken = FakeSource("rows.pushX({ nonsense })")
    assert ingest_source(broken, client=object()).ok is False
    assert _rows(PollenReading) == []

    # Now "fix" the parser by making the stored body parseable.
    with Session(get_engine()) as session:
        snapshot = session.exec(select(RawSnapshot)).one()
        snapshot.body = GOOD_BODY
        session.add(snapshot)
        session.commit()

    reports = reparse_snapshots("pmd_pollen", only_failed=True)

    assert len(reports) == 1 and reports[0].ok
    assert len(_rows(PollenReading)) == 2

    with Session(get_engine()) as session:
        assert session.exec(select(RawSnapshot)).one().parse_ok is True


def test_a_parse_failure_counts_against_health(db):
    """A 200 OK we cannot read is a failure, not a success with zero rows."""
    ingest_source(FakeSource("<html>unparseable</html>"), client=object())

    outcomes = _rows(FetchOutcome)
    assert len(outcomes) == 1
    assert outcomes[0].ok is False
    assert outcomes[0].failure_stage == "parse"


def test_a_fetch_failure_is_recorded_and_does_not_raise(db):
    report = ingest_source(FakeSource("", fetch_error="connection reset"), client=object())

    assert report.ok is False and report.stage == "fetched"
    outcomes = _rows(FetchOutcome)
    assert outcomes[0].failure_stage == "network"
    assert _rows(RawSnapshot) == [], "nothing was fetched, so there is nothing to store"


def test_unavailable_source_is_skipped_not_failed(db):
    """A missing API key is a configuration state, not an outage to alert on."""
    from hawa.sources.purpleair import PurpleAirSource

    report = ingest_source(PurpleAirSource(), client=object())

    assert report.ok is True
    assert report.stage == "skipped"
    assert "PURPLEAIR_API_KEY" in (report.skipped_reason or "")
    assert _rows(FetchOutcome) == [], "a skip must not pollute the health window"


@pytest.mark.parametrize("attempts", [1, 3])
def test_repeated_polling_of_an_unchanged_page_keeps_one_snapshot(db, attempts):
    for _ in range(attempts):
        ingest_source(FakeSource(GOOD_BODY), client=object())
    assert len(_rows(RawSnapshot)) == 1
