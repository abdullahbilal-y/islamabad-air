"""The contract every data source implements.

Adding a source is the main way to contribute to this project, so the contract
is deliberately small: fetch bytes, then turn bytes into readings. Those are
two separate methods on purpose --

* ``fetch`` is the only part allowed to touch the network, and whatever it
  returns is persisted *before* ``parse`` runs. If your parser is wrong, the
  bytes are still on disk and ``hawa reparse`` will replay them once you fix it.
* ``parse`` must be pure: same input, same output, no I/O. That is what makes
  it testable from a saved fixture, and every source in this repo ships one.

See CONTRIBUTING.md for the checklist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from hawa.config import get_config


class SourceUnavailable(RuntimeError):
    """The source cannot run right now (e.g. no API key). Not a failure to alert on."""


class FetchError(RuntimeError):
    """Network or HTTP-level failure. Distinguished from a parse failure."""


@dataclass(slots=True)
class Fetched:
    """Raw bytes plus enough context to store and later replay them."""

    url: str
    status_code: int
    body: str
    fetched_at: datetime


@dataclass(slots=True)
class PollenObservation:
    observed_date: date
    sector: str
    pollen_type: str
    value_raw: str | None
    value: int | None
    source_category: str | None = None
    needs_review: bool = False
    review_reason: str | None = None
    unit: str = "grains/m3"


@dataclass(slots=True)
class AirObservation:
    sensor_id: str
    metric: str
    observed_at: datetime
    value: float | None
    value_raw: str | None = None
    unit: str | None = None
    sensor_name: str | None = None
    sector: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(slots=True)
class ParseResult:
    pollen: list[PollenObservation] = field(default_factory=list)
    air: list[AirObservation] = field(default_factory=list)
    #: Which parsing path succeeded. Recorded so a silent shape change is visible.
    strategy: str = "default"

    @property
    def row_count(self) -> int:
        return len(self.pollen) + len(self.air)


class Source(ABC):
    """Base class for a data source."""

    #: Stable id used in the DB, the API and the health report. Never rename it.
    id: str = "unnamed"
    #: Human name for the dashboard.
    name: str = "Unnamed source"
    #: Where the data comes from, for attribution.
    attribution: str = ""
    #: What this source produces: {"pollen"} and/or {"air"}.
    provides: frozenset[str] = frozenset()

    def available(self) -> tuple[bool, str | None]:
        """Whether this source can run. Return ``(False, reason)`` to skip it."""
        return True, None

    @abstractmethod
    def fetch(self, client: httpx.Client) -> Fetched:
        """Do the network call. Raise :class:`FetchError` on failure."""

    @abstractmethod
    def parse(self, fetched: Fetched) -> ParseResult:
        """Turn a fetched body into observations. Must be pure and offline.

        Raise if the body cannot be understood -- an empty result and a broken
        parser are indistinguishable to the caller, so do not return an empty
        result to signal a problem.
        """


def build_client(**kwargs) -> httpx.Client:
    """An httpx client with our identifying UA and timeout.

    We send a real User-Agent naming the project and how to reach us. These are
    public government pages; being identifiable is the polite minimum and makes
    it possible for them to ask us to back off rather than just blocking us.
    """
    config = get_config()
    headers = {"User-Agent": config.user_agent, "Accept": "text/html,application/json"}
    headers.update(kwargs.pop("headers", {}))
    return httpx.Client(
        timeout=config.http_timeout_seconds,
        follow_redirects=True,
        headers=headers,
        **kwargs,
    )
