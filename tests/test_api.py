"""API contract tests, including the smoke test that the app actually boots."""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session

from hawa.db import get_engine
from hawa.models import FetchOutcome, PollenReading, utcnow

DAY = date(2026, 4, 1)


def _seed(**overrides):
    defaults = {
        "source": "pmd_pollen",
        "observed_date": DAY,
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


def test_app_boots_and_serves_the_dashboard(client):
    """A syntax check is not a runtime check -- CI must actually start the app.

    Everything below passes with a broken lifespan, a missing template or an
    unroutable dependency. This one does not.
    """
    response = client.get("/")
    assert response.status_code == 200
    assert "Islamabad" in response.text


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/v1/pollen/latest" in schema["paths"]
    assert "/v1/sources" in schema["paths"]


def test_livez_is_independent_of_data_health(client):
    assert client.get("/livez").json()["status"] == "alive"


def test_latest_says_503_when_there_is_no_data(client):
    """Never answer 'no pollen today' when the truth is 'we have nothing'."""
    response = client.get("/v1/pollen/latest")
    assert response.status_code == 503
    assert "hawa ingest" in response.json()["detail"]


def test_latest_returns_the_day_with_a_summary(client):
    _seed()
    _seed(pollen_type="Pines", value=40, value_raw="40", source_category="Moderate")

    body = client.get("/v1/pollen/latest").json()

    assert body["observed_date"] == DAY.isoformat()
    assert len(body["readings"]) == 2
    summary = body["sectors"][0]
    assert summary["sector"] == "H-8"
    assert summary["total"] == 46_172
    assert summary["band"] == "very_high"
    assert summary["top_type"] == "Paper Mulberry"
    assert "Pakistan Meteorological Department" in body["attribution"]


def test_raw_value_is_exposed_next_to_the_parsed_one(client):
    _seed(value=None, value_raw="1O2", needs_review=True)

    reading = client.get("/v1/pollen/latest").json()["readings"][0]
    assert reading["value"] is None
    assert reading["value_raw"] == "1O2"
    assert reading["needs_review"] is True


def test_date_range_query_filters_by_sector(client):
    _seed()
    _seed(sector="G-6", value=1_000, value_raw="1000")

    rows = client.get(f"/v1/pollen?start={DAY}&end={DAY}&sector=G-6").json()
    assert len(rows) == 1
    assert rows[0]["sector"] == "G-6"


def test_range_rejects_a_backwards_window(client):
    response = client.get("/v1/pollen?start=2026-05-01&end=2026-04-01")
    assert response.status_code == 400


def test_unknown_date_is_404(client):
    _seed()
    assert client.get("/v1/pollen/2020-01-01").status_code == 404


def test_sources_endpoint_reports_availability(client):
    sources = {s["id"]: s for s in client.get("/v1/sources").json()}

    assert sources["pmd_pollen"]["enabled"] is True
    assert sources["pmd_pollen"]["available"] is True
    # No key configured in tests, so the keyed sources report why they can't run.
    assert sources["purpleair"]["available"] is False
    assert "API_KEY" in sources["purpleair"]["unavailable_reason"]


def test_healthz_is_503_when_a_source_is_failing(client):
    with Session(get_engine()) as session:
        for _ in range(4):
            session.add(
                FetchOutcome(source="pmd_pollen", ok=False, failure_stage="parse", detail="boom")
            )
        session.commit()

    response = client.get("/healthz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["sources"][0]["reasons"]


def test_healthz_is_503_when_data_has_gone_stale(client):
    """Every fetch succeeding while the data is a week old is still broken."""
    _seed(observed_date=date.today() - timedelta(days=10))
    with Session(get_engine()) as session:
        for _ in range(4):
            session.add(FetchOutcome(source="pmd_pollen", ok=True, rows=8))
        session.commit()

    body = client.get("/healthz").json()
    assert body["status"] == "unhealthy"
    assert any("stale" in reason for reason in body["sources"][0]["reasons"])


def test_healthz_is_ok_with_fresh_data_and_successful_fetches(client):
    _seed(observed_date=utcnow().date())
    with Session(get_engine()) as session:
        for _ in range(4):
            session.add(FetchOutcome(source="pmd_pollen", ok=True, rows=8))
        session.commit()

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_subscribe_then_unsubscribe(client):
    created = client.post(
        "/v1/subscriptions",
        json={"channel": "webhook", "target": "https://example.test/hook", "sectors": "H-8"},
    )
    assert created.status_code == 201
    sub_id = created.json()["id"]

    # Re-subscribing is idempotent rather than an error.
    again = client.post(
        "/v1/subscriptions", json={"channel": "webhook", "target": "https://example.test/hook"}
    )
    assert again.status_code == 201
    assert again.json()["id"] == sub_id

    assert client.delete(f"/v1/subscriptions/{sub_id}").status_code == 204


def test_subscribe_rejects_a_bad_channel_and_a_bad_target(client):
    bad_channel = client.post(
        "/v1/subscriptions", json={"channel": "carrier-pigeon", "target": "x"}
    )
    assert bad_channel.status_code == 400
    assert (
        client.post(
            "/v1/subscriptions", json={"channel": "webhook", "target": "not-a-url"}
        ).status_code
        == 400
    )
