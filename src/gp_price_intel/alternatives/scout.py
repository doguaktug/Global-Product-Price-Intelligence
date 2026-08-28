"""Close-alternative scout stub with guarded value tests."""

from __future__ import annotations

from gp_price_intel.domain.models import Alternative, Offer


class AlternativeScout:
    def select(
        self,
        near_offers: list[Offer],
        best_offer: Offer | None,
        max_alternatives: int = 3,
    ) -> list[Alternative]:
        # Week 2: upgrade/downgrade value tests + comparable-product overlap.
        return []
