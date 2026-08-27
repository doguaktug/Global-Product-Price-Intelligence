"""Fixture adapter — returns empty list until fixtures are filled."""

from __future__ import annotations

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.domain.models import (
    AcquisitionMethod,
    Offer,
    SearchScope,
    Source,
    SourceKind,
)


class FixtureAdapter(SourceAdapter):
    def __init__(self, source: Source | None = None) -> None:
        self.source = source or Source(
            id="fixture-generic",
            display_name="Fixture",
            country="TR",
            kind=SourceKind.OTHER,
            reliability=0.5,
            acquisition_method=AcquisitionMethod.FIXTURE,
        )

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        # Week 2: load from data/fixtures when demos need multi-country offers.
        return []
