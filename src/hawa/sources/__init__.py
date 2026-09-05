"""Source registry.

To add a source: implement :class:`~hawa.sources.base.Source`, ship a saved
response in ``tests/fixtures/``, add a parser test, and register it here.
Nothing else in the codebase needs to change.
"""

from __future__ import annotations

from hawa.sources.base import (
    AirObservation,
    Fetched,
    FetchError,
    ParseResult,
    PollenObservation,
    Source,
    SourceUnavailable,
    build_client,
)
from hawa.sources.openaq import OpenAqSource
from hawa.sources.pmd_pollen import PmdPollenSource
from hawa.sources.purpleair import PurpleAirSource

REGISTRY: dict[str, Source] = {
    source.id: source
    for source in (PmdPollenSource(), PurpleAirSource(), OpenAqSource())
}


def get_source(source_id: str) -> Source:
    try:
        return REGISTRY[source_id]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown source {source_id!r}; known sources: {known}") from None


def all_sources() -> list[Source]:
    return list(REGISTRY.values())


__all__ = [
    "REGISTRY",
    "AirObservation",
    "FetchError",
    "Fetched",
    "OpenAqSource",
    "ParseResult",
    "PmdPollenSource",
    "PollenObservation",
    "PurpleAirSource",
    "Source",
    "SourceUnavailable",
    "all_sources",
    "build_client",
    "get_source",
]
