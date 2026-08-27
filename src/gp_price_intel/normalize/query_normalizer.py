"""Query normalization against the reference catalog (stub)."""

from __future__ import annotations

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import NormalizedQuery


class QueryNormalizer:
    """Parse user text → structured attributes + confirmation prompts."""

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()

    def normalize(self, raw_text: str) -> NormalizedQuery:
        # Week 2: typo fix, family match, identity/optional property prompts.
        return NormalizedQuery(
            raw_text=raw_text.strip(),
            extracted={},
            needs_confirmation=False,
            pending_properties=[],
        )
