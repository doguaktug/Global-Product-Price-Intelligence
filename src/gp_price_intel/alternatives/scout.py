"""Close-alternative scout with basic guarded selection."""

from __future__ import annotations

from decimal import Decimal

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    Alternative,
    AlternativeKind,
    Explanation,
    ExplanationReason,
    MatchKind,
    Offer,
    ProductVariant,
)


def _format(value: object) -> str:
    return "—" if value is None else str(value)


class AlternativeScout:
    """
    Pick close (same-family spec variant) and far (comparable product) alternatives.

    Spec comparison uses matcher output (`match_kind` / `match_notes`), not a
    Decision Page "best specifications" highlight — identical-product offers
    share the same specs, so that lens is not a ranking highlight.
    """

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()

    def select(
        self,
        near_offers: list[Offer],
        best_offer: Offer | None,
        confirmed_variant: ProductVariant | None = None,
        max_alternatives: int = 3,
    ) -> list[Alternative]:
        if best_offer is None or not near_offers:
            return []

        base_cost = self._landed_amount(best_offer)
        currency = self._reference_currency(best_offer)
        candidates: list[Alternative] = []

        for offer in near_offers:
            if offer.id == best_offer.id:
                continue
            if offer.match_kind == MatchKind.SIMILAR:
                delta = self._landed_amount(offer) - base_cost
                differences = self._spec_differences(confirmed_variant, offer)
                candidates.append(
                    Alternative(
                        offer_id=offer.id,
                        kind=AlternativeKind.SPEC_VARIANT,
                        differing_attributes=[key for key, _ in differences],
                        landed_cost_delta=offer.landed_cost.total if offer.landed_cost else None,
                        explanation=Explanation(
                            headline=f"Same family, different build: {offer.listing_title}",
                            reasons=[
                                ExplanationReason(factor=key, detail=detail)
                                for key, detail in differences
                            ],
                            caveats=[
                                f"Landed cost delta ≈ {delta} {currency} vs your top pick."
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

    def _spec_differences(
        self,
        confirmed_variant: ProductVariant | None,
        offer: Offer,
    ) -> list[tuple[str, str]]:
        """
        Which specs this build changes, as (spec key, "confirmed → alternative").

        Falls back to the matcher's notes when the offer never resolved to a
        catalog variant, so an alternative is never shown without a reason.
        """
        variant = (
            self.catalog.get_variant(offer.matched_variant_id)
            if offer.matched_variant_id
            else None
        )
        if confirmed_variant is None or variant is None:
            return [("match", note) for note in offer.match_notes]

        differences: list[tuple[str, str]] = []
        for key in self._comparable_keys(variant):
            confirmed_value = confirmed_variant.attribute(key)
            variant_value = variant.attribute(key)
            if confirmed_value != variant_value:
                differences.append(
                    (key, f"{_format(confirmed_value)} → {_format(variant_value)}")
                )
        return differences or [("match", note) for note in offer.match_notes]

    def _comparable_keys(self, variant: ProductVariant) -> list[str]:
        family = self.catalog.get_family(variant.family_id)
        category = self.catalog.get_category(family.category_id) if family else None
        if category is None:
            return []
        keys: list[str] = []
        for key in category.identity_keys + category.optional_keys + category.core_spec_keys:
            if key not in keys:
                keys.append(key)
        return keys

    @staticmethod
    def _reference_currency(offer: Offer) -> str:
        if offer.landed_cost:
            return offer.landed_cost.total.currency
        if offer.converted_list_price:
            return offer.converted_list_price.reference.currency
        return offer.list_price.currency

    @staticmethod
    def _landed_amount(offer: Offer) -> Decimal:
        if offer.landed_cost:
            return offer.landed_cost.total.amount
        if offer.converted_list_price:
            return offer.converted_list_price.reference.amount
        return offer.list_price.amount
