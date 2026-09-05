"""Alert engine: thresholds, dedup, and delivery retry semantics."""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import Session, select

from hawa.alerts.engine import band_for, dispatch_pending, evaluate
from hawa.db import get_engine
from hawa.models import AlertEvent, PollenReading, Subscription

DAY = date(2026, 4, 1)


def _seed_pollen(session: Session, sector: str, values: dict[str, int]) -> None:
    for pollen_type, value in values.items():
        session.add(
            PollenReading(
                source="pmd_pollen",
                observed_date=DAY,
                sector=sector,
                pollen_type=pollen_type,
                value=value,
                value_raw=str(value),
            )
        )
    session.commit()


def _subscribe(session: Session, **kwargs) -> Subscription:
    sub = Subscription(channel="webhook", target="https://example.test/hook", **kwargs)
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


def test_band_thresholds():
    assert band_for(0) == "absent"
    assert band_for(1_500) == "low"
    assert band_for(9_000) == "moderate"
    assert band_for(20_000) == "high"
    assert band_for(46_132) == "very_high"


def test_alert_fires_above_the_threshold(db):
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_000, "Pines": 132})
        _subscribe(session)

        created = evaluate(session, DAY)

    assert len(created) == 1
    event = created[0]
    assert event.kind == "pollen"
    assert event.sector == "H-8"
    assert event.value == pytest.approx(46_132)
    assert "Paper Mulberry" in event.trigger, "the alert should name the driving allergen"


def test_no_alert_below_the_threshold(db):
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Pines": 40})
        _subscribe(session)
        assert evaluate(session, DAY) == []


def test_reevaluating_the_same_day_does_not_re_alert(db):
    """The scheduler runs every 30 minutes; PMD publishes once a day.

    Without dedup this subscriber would get the same message 48 times.
    """
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _subscribe(session)

        assert len(evaluate(session, DAY)) == 1
        assert evaluate(session, DAY) == []
        assert evaluate(session, DAY) == []

        assert len(list(session.exec(select(AlertEvent)))) == 1


def test_sector_filter_is_respected(db):
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _seed_pollen(session, "G-6", {"Paper Mulberry": 30_000})
        _subscribe(session, sectors="G-6")

        created = evaluate(session, DAY)

    assert [e.sector for e in created] == ["G-6"]


def test_per_subscriber_threshold_overrides_the_global_one(db):
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Grasses": 3_000})
        _subscribe(session, pollen_threshold=1_000, label="sensitive")

        created = evaluate(session, DAY)

    assert len(created) == 1


def test_two_subscribers_each_get_their_own_alert(db):
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _subscribe(session, label="a")
        session.add(
            Subscription(channel="webhook", target="https://other.test/hook", label="b")
        )
        session.commit()

        created = evaluate(session, DAY)

    assert len(created) == 2
    assert len({e.dedup_key for e in created}) == 2


def test_alerts_disabled_switch_stops_evaluation(db):
    from hawa import settings_store

    settings_store.set_value("alerts_enabled", False)
    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _subscribe(session)
        assert evaluate(session, DAY) == []


def test_delivery_failure_leaves_the_alert_retryable(db, monkeypatch):
    """A broken channel must not silently consume the alert."""
    calls = {"n": 0}

    def failing_send(sub, event):
        calls["n"] += 1
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("hawa.alerts.channels.send_alert", failing_send)

    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _subscribe(session)
        evaluate(session, DAY)

        delivered, failed = dispatch_pending(session)
        assert (delivered, failed) == (0, 0), "first failure is a retry, not a give-up"

        event = session.exec(select(AlertEvent)).one()
        assert event.status == "pending"
        assert event.attempts == 1
        assert "telegram is down" in (event.last_error or "")

        # Retries continue until the cap, then the row is parked as failed.
        for _ in range(10):
            dispatch_pending(session)

        session.refresh(event)
        assert event.status == "failed"
        assert calls["n"] == 5, "retries stop at MAX_DELIVERY_ATTEMPTS"


def test_successful_delivery_marks_the_alert(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "hawa.alerts.channels.send_alert", lambda sub, event: sent.append((sub, event))
    )

    with Session(get_engine()) as session:
        _seed_pollen(session, "H-8", {"Paper Mulberry": 46_132})
        _subscribe(session)
        evaluate(session, DAY)

        delivered, failed = dispatch_pending(session)
        assert (delivered, failed) == (1, 0)

        event = session.exec(select(AlertEvent)).one()
        assert event.status == "delivered"
        assert event.delivered_at is not None

    assert len(sent) == 1


def test_message_names_the_city_and_the_project(db):
    from hawa.alerts.channels import format_message

    event = AlertEvent(
        subscription_id=1,
        dedup_key="k",
        kind="pollen",
        sector="H-8",
        trigger="Pollen 46,132 grains/m3 in H-8",
        value=46132,
        threshold=20000,
    )
    message = format_message(event)
    assert "H-8" in message
    assert "Islamabad" in message
