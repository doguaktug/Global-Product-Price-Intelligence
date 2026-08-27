"""Product matching stub — identical / similar / different."""

from __future__ import annotations

from gp_price_intel.domain.models import MatchKind, Offer, SearchScope


class ProductMatcher:
    def match(self, offers: list[Offer], scope: SearchScope) -> list[Offer]:
        # Week 2: identity → attributes → text hybrid matching.
        return offers
