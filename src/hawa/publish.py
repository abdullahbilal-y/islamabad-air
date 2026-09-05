"""Publish the dataset as static files.

Why static files instead of a server
------------------------------------
PMD reports about 32 rows on a busy day. A year of this dataset is roughly one
megabyte. At that size a database server, a hosting bill and an always-on
process buy you nothing that a JSON file on a CDN does not already give you —
and a static file has properties a small self-hosted API does not: it cannot go
down, cannot fall behind on security patches, costs nothing, and is trivially
mirrored by anyone who forks the repo.

So the published artifacts are plain files under ``docs/``, served by GitHub
Pages. ``docs/data/*.json`` *is* the public API.

The other half of the reason is durability. The ingest machine is somebody's
laptop; laptops die. Committing the parsed dataset to git means the archive
lives in every clone and on GitHub, not on one disk. That is the thing worth
protecting — raw snapshots are recoverable evidence, but the parsed history is
the dataset nobody else has.

What is NOT published
---------------------
Subscriptions and alert history stay local. They contain people's Telegram chat
ids and webhook URLs, and publishing those to a public repo would be a
straightforward privacy leak. :func:`publish_all` never reads those tables.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlmodel import Session, col, select

from hawa import __version__
from hawa.alerts.engine import band_for
from hawa.models import PollenReading, utcnow

log = logging.getLogger(__name__)

ATTRIBUTION = (
    "Pollen counts measured and published by the Pakistan Meteorological "
    "Department (weather.gov.pk). Digitised by Hawa, an independent "
    "open-source project - not an official PMD service, and not medical advice."
)
SOURCE_URL = "https://weather.gov.pk/rnd/pollen-data"
REPO_URL = "https://github.com/abdullahbilal-y/islamabad-air"


@dataclass(slots=True)
class PublishReport:
    files: list[Path]
    readings: int
    days: int
    first_date: date | None
    last_date: date | None

    def summary(self) -> str:
        if not self.readings:
            return "nothing to publish (no readings stored yet)"
        span = f"{self.first_date} to {self.last_date}"
        return (
            f"published {self.readings} readings across {self.days} day(s), {span} "
            f"-> {len(self.files)} file(s)"
        )


def _reading_dict(r: PollenReading) -> dict:
    return {
        "observed_date": r.observed_date.isoformat(),
        "sector": r.sector,
        "pollen_type": r.pollen_type,
        "value": r.value,
        "value_raw": r.value_raw,
        "unit": r.unit,
        "source_category": r.source_category,
        "needs_review": r.needs_review,
        "source": r.source,
    }


def _summarise(rows: list[dict]) -> list[dict]:
    """Per-sector totals and bands for one day's rows."""
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_sector[row["sector"]].append(row)

    out: list[dict] = []
    for sector, sector_rows in sorted(by_sector.items()):
        numeric = [r for r in sector_rows if r["value"] is not None]
        total = sum(int(r["value"]) for r in numeric)
        top = max(numeric, key=lambda r: r["value"], default=None)
        out.append(
            {
                "sector": sector,
                "total": total,
                "band": band_for(total),
                "top_type": top["pollen_type"] if top else None,
                "top_value": top["value"] if top else None,
                "types_reported": len(numeric),
            }
        )
    return out


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys and a trailing newline keep git diffs minimal and reviewable --
    # this file is committed on every run, so churn is a real cost.
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _write_if_changed(path, text)
    return path


def _write_if_changed(path: Path, text: str) -> bool:
    """Write only when the content actually differs.

    The publisher runs on every poll, which is far more often than PMD updates.
    Rewriting identical files would produce an empty commit (or a noisy one that
    only changes a timestamp) on every single run, burying the days that
    genuinely changed.
    """
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def publish_all(session: Session, out_dir: Path) -> PublishReport:
    """Write the whole dataset to ``out_dir`` as static files."""
    rows = [
        _reading_dict(r)
        for r in session.exec(
            select(PollenReading).order_by(
                col(PollenReading.observed_date),
                col(PollenReading.sector),
                col(PollenReading.pollen_type),
            )
        )
    ]

    data_dir = out_dir / "data"
    written: list[Path] = []

    if not rows:
        return PublishReport(files=[], readings=0, days=0, first_date=None, last_date=None)

    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_day[row["observed_date"]].append(row)

    days = sorted(by_day)
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])

    # --- latest.json: the one endpoint most consumers need -----------------
    latest_rows = by_day[days[-1]]
    written.append(
        _write_json(
            data_dir / "latest.json",
            {
                "observed_date": days[-1],
                "generated_at": utcnow().isoformat(),
                "sectors": _summarise(latest_rows),
                "readings": latest_rows,
                "attribution": ATTRIBUTION,
                "source_url": SOURCE_URL,
            },
        )
    )

    # --- one file per month, so a client can fetch a season ----------------
    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_month[row["observed_date"][:7]].append(row)

    for month, month_rows in sorted(by_month.items()):
        month_days = sorted({r["observed_date"] for r in month_rows})
        written.append(
            _write_json(
                data_dir / "months" / f"{month}.json",
                {
                    "month": month,
                    "days": month_days,
                    "readings": month_rows,
                    "daily_summaries": {
                        day: _summarise([r for r in month_rows if r["observed_date"] == day])
                        for day in month_days
                    },
                    "attribution": ATTRIBUTION,
                },
            )
        )

    # --- index.json: the manifest, so clients discover what exists ---------
    written.append(
        _write_json(
            data_dir / "index.json",
            {
                "generated_at": utcnow().isoformat(),
                "generator": f"hawa {__version__}",
                "first_date": days[0],
                "last_date": days[-1],
                "days_held": len(days),
                "total_readings": len(rows),
                "sectors": sorted({r["sector"] for r in rows}),
                "pollen_types": sorted({r["pollen_type"] for r in rows}),
                "months": sorted(by_month),
                "endpoints": {
                    "latest": "data/latest.json",
                    "month": "data/months/{YYYY-MM}.json",
                    "csv": "data/pollen.csv",
                    "manifest": "data/index.json",
                },
                "attribution": ATTRIBUTION,
                "source_url": SOURCE_URL,
                "repository": REPO_URL,
            },
        )
    )

    # --- the whole history as one CSV, for people with a spreadsheet -------
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "observed_date",
            "sector",
            "pollen_type",
            "value",
            "value_raw",
            "unit",
            "source_category",
            "needs_review",
            "source",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["observed_date"],
                row["sector"],
                row["pollen_type"],
                "" if row["value"] is None else row["value"],
                row["value_raw"] or "",
                row["unit"],
                row["source_category"] or "",
                int(row["needs_review"]),
                row["source"],
            ]
        )
    csv_path = data_dir / "pollen.csv"
    _write_if_changed(csv_path, buffer.getvalue())
    written.append(csv_path)

    return PublishReport(
        files=written,
        readings=len(rows),
        days=len(days),
        first_date=first,
        last_date=last,
    )
