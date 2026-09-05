"""Publishing the dataset as static files."""

from __future__ import annotations

import csv
import json
from datetime import date

from sqlmodel import Session

from hawa.db import get_engine
from hawa.models import PollenReading, Subscription
from hawa.publish import publish_all


def _seed(session: Session, **overrides) -> None:
    defaults = {
        "source": "pmd_pollen",
        "observed_date": date(2026, 4, 1),
        "sector": "H-8",
        "pollen_type": "Paper Mulberry",
        "value": 46_132,
        "value_raw": "46132",
        "source_category": "Very High",
    }
    defaults.update(overrides)
    session.add(PollenReading(**defaults))
    session.commit()


def test_publishes_nothing_when_there_is_no_data(db, tmp_path):
    with Session(get_engine()) as session:
        report = publish_all(session, tmp_path)

    assert report.readings == 0
    assert report.files == []
    # An empty latest.json would look like a real "no pollen" answer.
    assert not (tmp_path / "data" / "latest.json").exists()


def test_writes_latest_month_manifest_and_csv(db, tmp_path):
    with Session(get_engine()) as session:
        _seed(session)
        _seed(session, pollen_type="Pines", value=40, value_raw="40")
        report = publish_all(session, tmp_path)

    assert report.readings == 2
    assert report.days == 1

    latest = json.loads((tmp_path / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["observed_date"] == "2026-04-01"
    assert len(latest["readings"]) == 2
    summary = latest["sectors"][0]
    assert summary["sector"] == "H-8"
    assert summary["total"] == 46_172
    assert summary["band"] == "very_high"
    assert summary["top_type"] == "Paper Mulberry"
    assert "Pakistan Meteorological Department" in latest["attribution"]

    month = json.loads(
        (tmp_path / "data" / "months" / "2026-04.json").read_text(encoding="utf-8")
    )
    assert month["month"] == "2026-04"
    assert month["days"] == ["2026-04-01"]

    manifest = json.loads((tmp_path / "data" / "index.json").read_text(encoding="utf-8"))
    assert manifest["first_date"] == "2026-04-01"
    assert manifest["last_date"] == "2026-04-01"
    assert manifest["total_readings"] == 2
    assert manifest["months"] == ["2026-04"]
    assert "latest" in manifest["endpoints"]

    rows = list(csv.DictReader((tmp_path / "data" / "pollen.csv").open(encoding="utf-8")))
    assert len(rows) == 2
    assert {r["value_raw"] for r in rows} == {"46132", "40"}


def test_latest_tracks_the_newest_day_across_months(db, tmp_path):
    with Session(get_engine()) as session:
        _seed(session, observed_date=date(2026, 3, 19))
        _seed(session, observed_date=date(2026, 4, 2), value=100, value_raw="100")
        publish_all(session, tmp_path)

    latest = json.loads((tmp_path / "data" / "latest.json").read_text(encoding="utf-8"))
    assert latest["observed_date"] == "2026-04-02"

    months = sorted(p.name for p in (tmp_path / "data" / "months").iterdir())
    assert months == ["2026-03.json", "2026-04.json"]


def test_republishing_unchanged_data_does_not_touch_files(db, tmp_path):
    """The poll runs far more often than PMD updates.

    If every run rewrote every file, each one would produce a commit whose only
    change is a timestamp -- burying the days that actually changed under noise.
    Only ``latest.json`` carries a generated_at, so only it may churn.
    """
    with Session(get_engine()) as session:
        _seed(session)
        publish_all(session, tmp_path)

        csv_path = tmp_path / "data" / "pollen.csv"
        month_path = tmp_path / "data" / "months" / "2026-04.json"
        before = {p: p.stat().st_mtime_ns for p in (csv_path, month_path)}

        publish_all(session, tmp_path)

    for path, mtime in before.items():
        assert path.stat().st_mtime_ns == mtime, f"{path.name} was rewritten unnecessarily"


def test_raw_values_survive_publication(db, tmp_path):
    """A value we could not parse must reach consumers as the raw text."""
    with Session(get_engine()) as session:
        _seed(session, value=None, value_raw="1O2", needs_review=True)
        publish_all(session, tmp_path)

    reading = json.loads(
        (tmp_path / "data" / "latest.json").read_text(encoding="utf-8")
    )["readings"][0]
    assert reading["value"] is None
    assert reading["value_raw"] == "1O2"
    assert reading["needs_review"] is True


def test_subscriptions_are_never_published(db, tmp_path):
    """Chat ids and webhook URLs must not end up in a public repo."""
    with Session(get_engine()) as session:
        session.add(
            Subscription(channel="telegram", target="123456789", label="someone's chat")
        )
        session.commit()
        _seed(session)
        # Publish into its own subdirectory so this scans the published tree
        # only -- the test's SQLite file legitimately contains the target, and
        # `*.db` is gitignored so it never reaches the repo.
        site = tmp_path / "site"
        publish_all(session, site)

    published = [p for p in site.rglob("*") if p.is_file()]
    assert published, "expected published files to scan"
    for path in published:
        content = path.read_text(encoding="utf-8", errors="replace")
        assert "123456789" not in content, f"leaked a subscriber target into {path.name}"
