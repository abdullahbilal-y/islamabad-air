"""Reference-grade air quality around Islamabad via OpenAQ v3.

OpenAQ aggregates official and research monitors, so it is the sanity check
against the community PurpleAir units -- when the two disagree wildly, one of
them is wrong and you want to know that before alerting anybody.

Needs a free key (``HAWA_OPENAQ_API_KEY``).

The N+1 trap this avoids
------------------------
The obvious way to use OpenAQ v3 is ``/v3/locations?coordinates=...`` and then
``/v3/locations/{id}/latest`` for each result. That is one HTTP round trip per
monitor, and it is the single easiest way to make this ingest slow and
rate-limited. Instead we call ``/v3/parameters/{id}/latest`` once per *metric*
with a radius filter -- two requests total, regardless of how many monitors
exist in the city.

Status: implemented against OpenAQ's documented v3 API but **not yet verified
against a live key** -- see brain/landmines.md #8.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from hawa.config import get_config
from hawa.sources.base import (
    AirObservation,
    Fetched,
    FetchError,
    ParseResult,
    Source,
)

BASE_URL = "https://api.openaq.org/v3"

# Islamabad city centre; radius is metres and 25 km covers the twin cities.
CENTRE = (33.6844, 73.0479)
RADIUS_METRES = 25_000

#: OpenAQ parameter id -> our metric name. Ids are stable in the v3 API.
PARAMETERS = {2: "pm25", 1: "pm10", 7: "no2", 10: "co", 9: "so2", 3: "o3"}

UNITS = {
    "pm25": "ug/m3",
    "pm10": "ug/m3",
    "no2": "ppm",
    "co": "ppm",
    "so2": "ppm",
    "o3": "ppm",
}


class OpenAqSource(Source):
    id = "openaq"
    name = "OpenAQ reference monitors (25 km around Islamabad)"
    attribution = "OpenAQ (openaq.org), CC BY 4.0"
    provides = frozenset({"air"})

    def available(self) -> tuple[bool, str | None]:
        if not get_config().openaq_api_key:
            return False, "HAWA_OPENAQ_API_KEY is not set"
        return True, None

    def fetch(self, client: httpx.Client) -> Fetched:
        key = get_config().openaq_api_key or ""
        headers = {"X-API-Key": key, "Accept": "application/json"}
        params: dict[str, str | int] = {
            "coordinates": f"{CENTRE[0]},{CENTRE[1]}",
            "radius": RADIUS_METRES,
            "limit": 1000,
        }

        # One call per metric, not per monitor. The bodies are stitched into a
        # single envelope so the whole cycle is one replayable snapshot.
        envelope: dict[str, object] = {"fetched_for": {}, "errors": {}}
        any_ok = False
        for parameter_id, metric in PARAMETERS.items():
            url = f"{BASE_URL}/parameters/{parameter_id}/latest"
            try:
                response = client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                envelope["errors"][metric] = str(exc)  # type: ignore[index]
                continue
            if response.status_code >= 400:
                envelope["errors"][metric] = f"HTTP {response.status_code}"  # type: ignore[index]
                continue
            try:
                envelope["fetched_for"][metric] = response.json()  # type: ignore[index]
            except json.JSONDecodeError as exc:
                envelope["errors"][metric] = f"invalid JSON: {exc}"  # type: ignore[index]
                continue
            any_ok = True

        if not any_ok:
            raise FetchError(f"every OpenAQ parameter call failed: {envelope['errors']}")

        return Fetched(
            url=f"{BASE_URL}/parameters/*/latest",
            status_code=200,
            body=json.dumps(envelope),
            fetched_at=datetime.now(timezone.utc),
        )

    def parse(self, fetched: Fetched) -> ParseResult:
        envelope = json.loads(fetched.body)
        per_metric = envelope.get("fetched_for", {})
        observations: list[AirObservation] = []

        for metric, payload in per_metric.items():
            for item in payload.get("results", []) or []:
                sensor_id = item.get("sensorsId") or item.get("locationsId")
                if sensor_id is None:
                    continue
                period = item.get("datetime") or {}
                stamp = period.get("utc") if isinstance(period, dict) else None
                if not stamp:
                    # No timestamp means we cannot say when this was true.
                    # Dropping it beats stamping it "now".
                    continue
                observed_at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=timezone.utc)

                coords = item.get("coordinates") or {}
                raw_value = item.get("value")
                try:
                    value = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    value = None

                observations.append(
                    AirObservation(
                        sensor_id=str(sensor_id),
                        sensor_name=(item.get("location") or {}).get("name")
                        if isinstance(item.get("location"), dict)
                        else None,
                        latitude=coords.get("latitude"),
                        longitude=coords.get("longitude"),
                        metric=metric,
                        value=value,
                        value_raw=str(raw_value),
                        unit=UNITS.get(metric),
                        observed_at=observed_at,
                    )
                )

        if not observations:
            raise ValueError(
                "OpenAQ returned no usable measurements -- no monitors reporting near "
                "Islamabad, or the v3 response shape changed"
            )
        return ParseResult(air=observations, strategy="openaq_v3_parameter_latest")
