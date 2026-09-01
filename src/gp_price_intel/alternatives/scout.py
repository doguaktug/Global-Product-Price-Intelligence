"""Close-alternative scout with basic guarded selection."""

from __future__ import annotations

from decimal import Decimal

from gp_price_intel.domain.models import (
    Alternative,
    AlternativeKind,
    Explanation,
    MatchKind,
    Offer,
)


class AlternativeScout:
    def select(
        self,
        near_offers: list[Offer],
        best_offer: Offer | None,
        max_alternatives: int = 3,
    ) -> list[Alternative]:
        if best_offer is None or not near_offers:
            return []

        base_cost = self._landed_amount(best_offer)
        candidates: list[Alternative] = []

        for offer in near_offers:
            if offer.id == best_offer.id:
                continue
            if offer.match_kind == MatchKind.SIMILAR:
                delta = self._landed_amount(offer) - base_cost
                candidates.append(
                    Alternative(
                        offer_id=offer.id,
                        kind=AlternativeKind.SPEC_VARIANT,
                        differing_attributes=offer.match_notes,
                        landed_cost_delta=offer.landed_cost.total if offer.landed_cost else None,
                        explanation=Explanation(
                            headline=f"Same family, different variant: {offer.listing_title}",
                            reasons=[],
                            caveats=[
                                f"Landed cost delta ≈ {delta} {best_offer.list_price.currency} vs your top pick."
                            ],
                        ),
                    )
                )
            elif offer.match_kind == MatchKind.DIFFERENT:
                candidates.append(
                    Alternative(
                        offer_id=offer.id,
                        kind=AlternativeKind.COMPARABLE_PRODUCT,
                        explanation=Explanation(
                            headline=f"Comparable product: {offer.listing_title}",
                            reasons=[],
                            caveats=["Different product family — review specs carefully."],
                        ),
                    )
                )

        return candidates[:max_alternatives]

    @staticmethod
    def _landed_amount(offer: Offer) -> Decimal:
        if offer.landed_cost:
            return offer.landed_cost.total.amount
        if offer.converted_list_price:
            return offer.converted_list_price.reference.amount
        return offer.list_price.amount
