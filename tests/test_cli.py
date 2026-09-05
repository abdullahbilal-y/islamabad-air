"""CLI tests.

These exist mostly because the CLI is where detached-ORM-object bugs hide: the
API layer serialises inside the request, so a command that reads rows and uses
them after the session closes can be broken while every other test passes.
"""

from __future__ import annotations

import csv
from datetime import date

from sqlmodel import Session
from typer.testing import CliRunner

from hawa.cli import app
from hawa.db import get_engine
from hawa.models import PollenReading

runner = CliRunner()


def _seed(**overrides):
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
    with Session(get_engine()) as session:
        session.add(PollenReading(**defaults))
        session.commit()


def test_version(db):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_export_writes_a_csv(db, tmp_path):
    _seed()
    _seed(pollen_type="Pines", value=40, value_raw="40")
    out = tmp_path / "pollen.csv"

    result = runner.invoke(app, ["export", "--out", str(out)])

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["sector"] == "H-8"
    # The raw text ships alongside the parsed number, so a doubtful reading
    # can still be checked against the source.
    assert {r["value_raw"] for r in rows} == {"46132", "40"}


def test_export_respects_the_date_window(db, tmp_path):
    _seed()
    _seed(observed_date=date(2026, 5, 1), pollen_type="Grasses", value=10, value_raw="10")
    out = tmp_path / "april.csv"

    runner.invoke(
        app, ["export", "--start", "2026-04-01", "--end", "2026-04-30", "--out", str(out)]
    )

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["observed_date"] == "2026-04-01"


def test_settings_roundtrip(db):
    assert runner.invoke(app, ["settings", "set", "pollen_alert_threshold", "9000"]).exit_code == 0

    result = runner.invoke(app, ["settings", "show"])
    assert result.exit_code == 0
    assert '"pollen_alert_threshold": 9000' in result.stdout


def test_settings_set_rejects_an_unknown_key(db):
    result = runner.invoke(app, ["settings", "set", "not_a_setting", "1"])
    assert result.exit_code == 1


def test_health_reports_unknown_before_any_fetch(db):
    result = runner.invoke(app, ["health"])
    # No attempts yet, so the source is "unknown" -- not a failure, but also
    # not a claim that everything is fine.
    assert '"status": "unknown"' in result.stdout


def test_subscribe_then_check_alerts(db):
    _seed()
    assert runner.invoke(app, ["subscribe", "webhook", "https://example.test/h"]).exit_code == 0

    result = runner.invoke(app, ["check-alerts", "--observed-date", "2026-04-01"])

    assert result.exit_code == 0
    assert "queued" in result.stdout
    assert "Paper Mulberry" in result.stdout
