"""PMD's daily Islamabad pollen counts.

This is the source the project exists for. PMD publishes sector-wise counts for
H-8, E-8, G-6 and F-10 on a page that renders its table in JavaScript, which is
why the numbers are effectively unavailable to anyone who is not scraping them.

Verified live on 2026-09-05: ``https://weather.gov.pk/rnd/pollen-data`` returns
the day's rows server-rendered inside the page's JS. The older
``rnd.pmd.gov.pk`` and ``namc.pmd.gov.pk`` hosts were both returning HTTP 500 at
that time -- hence the URL living in a settings row rather than in this file.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from hawa import settings_store
from hawa.parsers.pmd import parse_pmd_pollen
from hawa.sources.base import (
    Fetched,
    FetchError,
    ParseResult,
    PollenObservation,
    Source,
)


class PmdPollenSource(Source):
    id = "pmd_pollen"
    name = "Pakistan Meteorological Department - Islamabad pollen count"
    attribution = "Pakistan Meteorological Department (weather.gov.pk)"
    provides = frozenset({"pollen"})

    def fetch(self, client: httpx.Client) -> Fetched:
        url = settings_store.get("pmd_pollen_url")
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"GET {url} failed: {exc}") from exc

        if response.status_code >= 400:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")

        return Fetched(
            url=str(response.url),
            status_code=response.status_code,
            body=response.text,
            fetched_at=datetime.now(timezone.utc),
        )

    def parse(self, fetched: Fetched) -> ParseResult:
        parsed = parse_pmd_pollen(fetched.body)
        observations = [
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
        ]
        return ParseResult(pollen=observations, strategy=parsed.strategy)
