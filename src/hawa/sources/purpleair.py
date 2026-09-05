"""Community PurpleAir sensors inside the Islamabad bounding box.

Islamabad has a scattering of privately-run PurpleAir units, which between them
give far better spatial coverage of winter smog than the handful of official
monitors. PurpleAir's API needs a free key (``HAWA_PURPLEAIR_API_KEY``); without
one this source reports itself unavailable and is skipped rather than failing.

One deliberate design point: this makes **one** bounding-box call and reads
every sensor out of the response, instead of looping over sensors and calling
per sensor. A per-row remote call turns a 1-second refresh into a 60-second one
and gets you rate-limited, and it is the easy mistake to make here because
PurpleAir also exposes a tidy ``/v1/sensors/{index}`` endpoint.

Status: implemented against PurpleAir's documented v1 API but **not yet
verified against a live key** -- see brain/landmines.md #8.
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

API_URL = "https://api.purpleair.com/v1/sensors"

# Islamabad + Rawalpindi. North-west and south-east corners.
BBOX = {"nwlng": 72.80, "nwlat": 33.85, "selng": 73.35, "selat": 33.40}

FIELDS = [
    "sensor_index",
    "name",
    "latitude",
    "longitude",
    "last_seen",
    "pm2.5",
    "pm2.5_60minute",
    "pm10.0",
    "humidity",
]

#: PurpleAir field name -> our metric name.
METRIC_MAP = {
    "pm2.5": "pm25",
    "pm2.5_60minute": "pm25_60m",
    "pm10.0": "pm10",
    "humidity": "humidity",
}

METRIC_UNITS = {
    "pm25": "ug/m3",
    "pm25_60m": "ug/m3",
    "pm10": "ug/m3",
    "humidity": "%",
}


class PurpleAirSource(Source):
    id = "purpleair"
    name = "PurpleAir community sensors (Islamabad / Rawalpindi)"
    attribution = "PurpleAir, Inc. - community-operated sensors"
    provides = frozenset({"air"})

    def available(self) -> tuple[bool, str | None]:
        if not get_config().purpleair_api_key:
            return False, "HAWA_PURPLEAIR_API_KEY is not set"
        return True, None

    def fetch(self, client: httpx.Client) -> Fetched:
        key = get_config().purpleair_api_key
        params: dict[str, str | float] = {"fields": ",".join(FIELDS), **BBOX}
        try:
            response = client.get(API_URL, params=params, headers={"X-API-Key": key or ""})
        except httpx.HTTPError as exc:
            raise FetchError(f"GET {API_URL} failed: {exc}") from exc
        if response.status_code >= 400:
            raise FetchError(
                f"GET {API_URL} returned HTTP {response.status_code}: {response.text[:300]}"
            )
        return Fetched(
            url=str(response.url),
            status_code=response.status_code,
            body=response.text,
            fetched_at=datetime.now(timezone.utc),
        )

    def parse(self, fetched: Fetched) -> ParseResult:
        payload = json.loads(fetched.body)
        fields = payload.get("fields")
        rows = payload.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise ValueError("PurpleAir response has no fields/data arrays")

        index = {name: i for i, name in enumerate(fields)}
        required = ("sensor_index",)
        missing = [f for f in required if f not in index]
        if missing:
            raise ValueError(f"PurpleAir response missing fields: {missing}")

        def cell(row: list, field_name: str):
            pos = index.get(field_name)
            return row[pos] if pos is not None and pos < len(row) else None

        observations: list[AirObservation] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            sensor_index = cell(row, "sensor_index")
            if sensor_index is None:
                continue

            last_seen = cell(row, "last_seen")
            # PurpleAir reports last_seen as a unix timestamp. If it is absent
            # we do NOT substitute "now" -- that would stamp stale readings as
            # fresh and defeat the staleness half of the health check.
            if last_seen is None:
                continue
            observed_at = datetime.fromtimestamp(int(last_seen), tz=timezone.utc)

            for field_name, metric in METRIC_MAP.items():
                raw = cell(row, field_name)
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    # Keep the raw text and flag it by leaving value empty
                    # rather than inventing a number.
                    value = None
                observations.append(
                    AirObservation(
                        sensor_id=str(sensor_index),
                        sensor_name=cell(row, "name"),
                        latitude=cell(row, "latitude"),
                        longitude=cell(row, "longitude"),
                        metric=metric,
                        value=value,
                        value_raw=str(raw),
                        unit=METRIC_UNITS.get(metric),
                        observed_at=observed_at,
                    )
                )

        if not observations:
            raise ValueError(
                "PurpleAir returned no usable sensor rows -- either the bounding box "
                "has no active sensors or the response shape changed"
            )
        return ParseResult(air=observations, strategy="purpleair_v1_bbox")
