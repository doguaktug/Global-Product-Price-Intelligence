"""Product matching — identity (SKU/GTIN/model) then attributes."""

from __future__ import annotations

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import MatchKind, Offer, ProductVariant, SearchScope
from gp_price_intel.matching.identifiers import (
    code_matches,
    extract_offer_identifiers,
    gtin_matches,
    variant_retailer_sku,
)


class ProductMatcher:
    """
    Match live offers to catalog variants.

    Priority (per architecture):
    1. Identity — GTIN, manufacturer model number, per-source retailer SKU
    2. Attributes — same family + identity keys (storage, RAM, region, …)
    3. Unmatched — dropped from ranking later
    """

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()

    def match(self, offers: list[Offer], scope: SearchScope) -> list[Offer]:
        family = self.catalog.get_family(scope.family_id)
        if family is None:
            return offers

        family_variants = self.catalog.list_variants(scope.family_id)
        scope_variants = self._variants_in_scope(family_variants, scope)
        category = self.catalog.get_category(family.category_id)
        identity_keys = category.identity_keys if category else []
        optional_keys = category.optional_keys if category else []

        return [
            self._match_offer(
                offer, family_variants, scope_variants, identity_keys, optional_keys
            )
            for offer in offers
        ]

    def _variants_in_scope(
        self,
        family_variants: list[ProductVariant],
        scope: SearchScope,
    ) -> list[ProductVariant]:
        if scope.variant_ids:
            allowed = set(scope.variant_ids)
            return [variant for variant in family_variants if variant.id in allowed]
        return family_variants

    def _match_offer(
        self,
        offer: Offer,
        family_variants: list[ProductVariant],
        scope_variants: list[ProductVariant],
        identity_keys: list[str],
        optional_keys: list[str],
    ) -> Offer:
        by_identity = self._match_by_identifiers(offer, family_variants)
        if by_identity is not None:
            variant, note = by_identity
            scope_ids = {v.id for v in scope_variants}
            kind = MatchKind.IDENTICAL if variant.id in scope_ids else MatchKind.SIMILAR
            notes = [note]
            if kind == MatchKind.SIMILAR:
                notes.append("Identifier matched a different catalog variant than the confirmed scope.")
            return offer.model_copy(
                update={
                    "matched_variant_id": variant.id,
                    "match_kind": kind,
                    "match_notes": notes,
                }
            )

        by_attributes = self._match_by_attributes(
            offer, family_variants, identity_keys, optional_keys, scope_variants
        )
        if by_attributes is not None:
            variant, kind, notes = by_attributes
            return offer.model_copy(
                update={
                    "matched_variant_id": variant.id,
                    "match_kind": kind,
                    "match_notes": notes,
                }
            )

        return offer.model_copy(
            update={
                "match_kind": MatchKind.UNMATCHED,
                "match_notes": ["No SKU/GTIN/model match and attributes did not align."],
            }
        )

    def _match_by_identifiers(
        self,
        offer: Offer,
        variants: list[ProductVariant],
    ) -> tuple[ProductVariant, str] | None:
        ids = extract_offer_identifiers(offer)
        if not ids:
            return None

        offer_gtin = ids.get("gtin")
        offer_model = ids.get("model_number")
        offer_sku = ids.get("retailer_sku")

        for variant in variants:
            if gtin_matches(offer_gtin, variant.gtin):
                return variant, f"GTIN match ({offer_gtin})."

            if code_matches(offer_model, variant.model_number):
                return variant, f"Model number match ({offer_model})."

            catalog_sku = variant_retailer_sku(variant, offer.source_id)
            if offer_sku and catalog_sku and code_matches(offer_sku, catalog_sku):
                return variant, f"Retailer SKU match for {offer.source_id} ({offer_sku})."

        return None

    def _match_by_attributes(
        self,
        offer: Offer,
        variants: list[ProductVariant],
        identity_keys: list[str],
        optional_keys: list[str],
        scope_variants: list[ProductVariant],
    ) -> tuple[ProductVariant, MatchKind, list[str]] | None:
        """Fallback when no strong ID — compare parsed specs in raw_specs to variant fields."""
        spec_attrs = {
            spec.key: spec.value
            for spec in offer.raw_specs
            if spec.value is not None
        }
        keys_to_use = list(identity_keys) + [
            key for key in optional_keys if key in spec_attrs
        ]
        if not keys_to_use:
            return None

        offer_attrs = {key: spec_attrs[key] for key in keys_to_use if key in spec_attrs}
        if not offer_attrs:
            return None

        candidates: list[ProductVariant] = []

        for variant in variants:
            variant_values = {key: getattr(variant, key, None) for key in keys_to_use}

            conflicts = [
                key
                for key in offer_attrs
                if variant_values[key] is not None and offer_attrs[key] != variant_values[key]
            ]
            if conflicts:
                continue

            satisfied = [
                key
                for key in offer_attrs
                if variant_values[key] is not None and offer_attrs[key] == variant_values[key]
            ]
            if satisfied:
                candidates.append(variant)

        if len(candidates) == 1:
            variant = candidates[0]
            scope_ids = {v.id for v in scope_variants}
            kind = MatchKind.IDENTICAL if variant.id in scope_ids else MatchKind.SIMILAR
            notes = ["Attribute match on catalog fields."]
            if kind == MatchKind.SIMILAR:
                notes.append("Attributes matched a variant outside the confirmed scope.")
            return variant, kind, notes

        if len(candidates) > 1:
            return candidates[0], MatchKind.SIMILAR, ["Attribute match ambiguous across variants."]

        return None
