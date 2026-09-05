"""Command line interface: ``hawa <command>``."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

import typer

from hawa import __version__, settings_store
from hawa.db import init_db, session_scope

app = typer.Typer(
    help="Hawa - Islamabad pollen and air-quality data.", no_args_is_help=True
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the database tables."""
    init_db()
    typer.echo("Database ready.")


@app.command()
def ingest(
    source: str | None = typer.Option(None, help="Ingest one source instead of all enabled."),
) -> None:
    """Fetch, store and parse the enabled sources once."""
    from hawa.ingest import ingest_enabled_sources, ingest_source

    init_db()
    reports = [ingest_source(source)] if source else ingest_enabled_sources()
    if not reports:
        typer.echo("No sources enabled. Check `hawa settings show`.")
        raise typer.Exit(code=1)

    failed = 0
    for report in reports:
        typer.echo(report.summary())
        if not report.ok:
            failed += 1
    raise typer.Exit(code=1 if failed else 0)


@app.command()
def reparse(
    source: str | None = typer.Option(None, help="Limit to one source."),
    since: str | None = typer.Option(None, help="ISO date; only snapshots from then on."),
    all_snapshots: bool = typer.Option(
        False, "--all", help="Replay every snapshot, not just the ones that failed to parse."
    ),
) -> None:
    """Re-run the current parsers over stored raw snapshots.

    Use after fixing a parser: it repairs the days that the old parser got
    wrong, without re-fetching anything (which for a daily figure is
    impossible anyway -- yesterday's page is gone).
    """
    from hawa.ingest import reparse_snapshots

    init_db()
    since_date = date.fromisoformat(since) if since else None
    reports = reparse_snapshots(source, since=since_date, only_failed=not all_snapshots)
    if not reports:
        typer.echo("No snapshots matched.")
        return
    for report in reports:
        typer.echo(report.summary())


@app.command()
def health() -> None:
    """Show per-source outcome health."""
    from hawa.health import overall_health

    init_db()
    with session_scope() as session:
        report = overall_health(session, list(settings_store.get("enabled_sources") or []))
    typer.echo(json.dumps(report, indent=2))
    if report["status"] == "unhealthy":
        raise typer.Exit(code=1)


@app.command("check-alerts")
def check_alerts(
    observed_date: str | None = typer.Option(None, help="ISO date. Defaults to today (PKT)."),
    send: bool = typer.Option(False, "--send", help="Actually deliver, not just evaluate."),
) -> None:
    """Evaluate thresholds and optionally deliver the resulting alerts."""
    from hawa.alerts.engine import dispatch_pending, evaluate

    init_db()
    on = date.fromisoformat(observed_date) if observed_date else None
    with session_scope() as session:
        created = evaluate(session, on)
        for event in created:
            typer.echo(f"queued: {event.trigger}")
        if not created:
            typer.echo("No thresholds crossed.")

    if send:
        with session_scope() as session:
            delivered, failed = dispatch_pending(session)
        typer.echo(f"delivered={delivered} failed={failed}")


@app.command()
def subscribe(
    channel: str = typer.Argument(..., help="telegram | webhook"),
    target: str = typer.Argument(..., help="Telegram chat id, or an https:// URL."),
    sectors: str | None = typer.Option(None, help="Comma-separated, e.g. 'H-8,G-6'."),
    threshold: int | None = typer.Option(None, help="Override the pollen threshold."),
    label: str | None = typer.Option(None),
) -> None:
    """Add an alert subscription."""
    from hawa.models import Subscription

    init_db()
    with session_scope() as session:
        sub = Subscription(
            channel=channel,
            target=target,
            sectors=sectors,
            pollen_threshold=threshold,
            label=label,
        )
        session.add(sub)
    typer.echo(f"Subscribed {channel}:{target}")


settings_app = typer.Typer(help="Read and change runtime settings (stored in the DB).")
app.add_typer(settings_app, name="settings")


@settings_app.command("show")
def settings_show() -> None:
    """Print every runtime setting and its current value."""
    init_db()
    typer.echo(json.dumps(settings_store.all_settings(), indent=2, default=str))


@settings_app.command("set")
def settings_set(key: str, value: str) -> None:
    """Set a setting. VALUE is parsed as JSON, falling back to a plain string."""
    init_db()
    if key not in settings_store.DEFAULTS:
        known = ", ".join(sorted(settings_store.DEFAULTS))
        typer.echo(f"Unknown setting {key!r}. Known: {known}", err=True)
        raise typer.Exit(code=1)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    settings_store.set_value(key, parsed)
    typer.echo(f"{key} = {json.dumps(parsed)}")


@app.command()
def publish(
    out: str = typer.Option("docs", help="Directory to write the static site data into."),
) -> None:
    """Write the dataset to static JSON/CSV files.

    These files are the public API -- see src/hawa/publish.py for why a static
    file beats a server at this data volume.
    """
    from pathlib import Path

    from hawa.publish import publish_all

    init_db()
    with session_scope() as session:
        report = publish_all(session, Path(out))
    typer.echo(report.summary())


@app.command()
def poll(
    publish_to: str = typer.Option("docs", "--publish-to", help="Static output directory."),
    send: bool = typer.Option(True, help="Deliver any alerts that fire."),
) -> None:
    """One full cycle: ingest, evaluate alerts, republish. Run this on a schedule.

    Exits 0 even when a source fails. This is what a scheduler calls, and a
    non-zero exit on a transient PMD outage would light up as a failed task
    every time their server hiccups -- which is often. Real problems surface
    through `hawa health`, which is what you should alert on.
    """
    from pathlib import Path

    from hawa.alerts.engine import dispatch_pending, evaluate
    from hawa.ingest import ingest_enabled_sources
    from hawa.publish import publish_all

    init_db()

    for report in ingest_enabled_sources():
        typer.echo(report.summary())

    try:
        with session_scope() as session:
            created = evaluate(session)
        for event in created:
            typer.echo(f"alert: {event.trigger}")
        if send and created:
            with session_scope() as session:
                delivered, failed = dispatch_pending(session)
            typer.echo(f"alerts delivered={delivered} failed={failed}")
    except Exception as exc:
        # Alerting is best-effort; the data is already stored. Never let a
        # broken Telegram token stop the dataset being published.
        typer.echo(f"alert stage failed (data is unaffected): {exc}", err=True)

    with session_scope() as session:
        typer.echo(publish_all(session, Path(publish_to)).summary())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Use 0.0.0.0 in a container."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
) -> None:
    """Run the API and dashboard."""
    import uvicorn

    uvicorn.run("hawa.main:app", host=host, port=port, reload=reload)


@app.command()
def export(
    start: str | None = typer.Option(None, help="ISO date, inclusive."),
    end: str | None = typer.Option(None, help="ISO date, inclusive."),
    out: str = typer.Option("pollen.csv", help="Output CSV path."),
) -> None:
    """Export pollen readings to CSV.

    Bulk export matters more than it looks for this project: the point is that
    researchers can get the whole history in one file, not that they have to
    page an API to reconstruct it.
    """
    import csv

    from sqlmodel import col, select

    from hawa.models import PollenReading

    init_db()
    query = select(PollenReading)
    if start:
        query = query.where(PollenReading.observed_date >= date.fromisoformat(start))
    if end:
        query = query.where(PollenReading.observed_date <= date.fromisoformat(end))

    # Materialise the columns inside the session. session_scope() commits on
    # exit, which expires every loaded object, so reading attributes after the
    # block raises DetachedInstanceError.
    with session_scope() as session:
        rows = [
            (
                r.observed_date.isoformat(),
                r.sector,
                r.pollen_type,
                r.value if r.value is not None else "",
                r.value_raw or "",
                r.unit,
                r.source_category or "",
                int(r.needs_review),
                r.source,
                r.ingested_at.isoformat() if isinstance(r.ingested_at, datetime) else "",
            )
            for r in session.exec(query.order_by(col(PollenReading.observed_date)))
        ]

    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
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
                "ingested_at",
            ]
        )
        writer.writerows(rows)
    typer.echo(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    app()
