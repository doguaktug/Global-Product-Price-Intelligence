"""Product matching — identity (SKU/GTIN/model) then attributes."""

from __future__ import annotations

import re

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    MatchKind,
    Offer,
    ProductFamily,
    ProductVariant,
    SearchScope,
)
from gp_price_intel.matching.identifiers import (
    code_matches,
    extract_offer_identifiers,
    gtin_matches,
    normalize_gtin,
    variant_retailer_sku,
)
from gp_price_intel.normalize.family import catalog_vocabulary, sibling_family_outscores
from gp_price_intel.normalize.offer_labels import english_variant_label

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class ProductMatcher:
    """
    Match live offers to catalog variants.

    Priority (per architecture):
    1. Identity — GTIN, manufacturer model number, per-source retailer SKU
    2. Family name — listing title must uniquely name this family (Ultra ≠ Plus)
    3. Attributes — same family + identity keys (storage, RAM, region, …)
    4. Unmatched — dropped from ranking later
    """

    def __init__(self, catalog: CatalogRepository | None = None) -> None:
        self.catalog = catalog or CatalogRepository()
        self.vocabulary = catalog_vocabulary(self.catalog)

    def match(self, offers: list[Offer], scope: SearchScope) -> list[Offer]:
        family = self.catalog.get_family(scope.family_id)
        if family is None:
            return offers

        family_variants = self.catalog.list_variants(scope.family_id)
        scope_variants = self._variants_in_scope(family_variants, scope)
        category = self.catalog.get_category(family.category_id)
        identity_keys = category.identity_keys if category else []
        optional_keys = list(category.optional_keys) if category else []
        # Category spec keys (processor, display size, …) are corroborating evidence:
        # they never create a match on their own, but a stated conflict blocks one.
        comparison_keys = list(identity_keys) + [
            key
            for key in optional_keys + (category.core_spec_keys if category else [])
            if key not in identity_keys
        ]

        return [
            self._match_offer(
                offer,
                family,
                family_variants,
                scope_variants,
                identity_keys,
                comparison_keys,
                optional_keys,
                scope,
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
        family: ProductFamily,
        family_variants: list[ProductVariant],
        scope_variants: list[ProductVariant],
        identity_keys: list[str],
        comparison_keys: list[str],
        optional_keys: list[str],
        scope: SearchScope,
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
                    "display_name": english_variant_label(variant),
                }
            )

        if not self._listing_names_family(offer, family, family_variants):
            return offer.model_copy(
                update={
                    "match_kind": MatchKind.UNMATCHED,
                    "match_notes": [
                        "Listing names a different catalog model than the confirmed family."
                    ],
                }
            )

        by_attributes = self._match_by_attributes(
            offer,
            family_variants,
            identity_keys,
            comparison_keys,
            optional_keys,
            scope_variants,
            scope,
        )
        if by_attributes is not None:
            variant, kind, notes = by_attributes
            return offer.model_copy(
                update={
                    "matched_variant_id": variant.id,
                    "match_kind": kind,
                    "match_notes": notes,
                    "display_name": english_variant_label(variant),
                }
            )

        return offer.model_copy(
            update={
                "match_kind": MatchKind.UNMATCHED,
                "match_notes": ["No SKU/GTIN/model match and attributes did not align."],
            }
        )

    def _listing_names_family(
        self,
        offer: Offer,
        family: ProductFamily,
        family_variants: list[ProductVariant],
    ) -> bool:
        """
        False when the title uniquely names a sibling family (S26+ vs S26 Ultra).

        Query confirmation already locked the search family. Marketplace titles still
        mix siblings, and attribute matching would otherwise treat shared storage/RAM
        as an identical Ultra. The same distinctive-token scorer used on the user's
        query is applied to the title. A manufacturer code printed in the title is
        accepted even when the marketing name is omitted. Titles that name no sibling
        still fall through to attribute matching.
        """
        if _title_mentions_variant_identity(offer.listing_title, family_variants):
            return True
        return not sibling_family_outscores(
            offer.listing_title,
            family,
            self.catalog.list_families(),
            self.vocabulary,
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
        comparison_keys: list[str],
        optional_keys: list[str],
        scope_variants: list[ProductVariant],
        scope: SearchScope,
    ) -> tuple[ProductVariant, MatchKind, list[str]] | None:
        """Fallback when no strong ID — compare parsed specs in raw_specs to variant specs."""
        spec_attrs = {
            spec.key: spec.value
            for spec in offer.raw_specs
            if spec.value is not None
        }
        keys_to_use = list(identity_keys) + [
            key
            for key in comparison_keys
            if key not in identity_keys and key in spec_attrs
        ]
        if not keys_to_use:
            return None

        offer_attrs = {key: spec_attrs[key] for key in keys_to_use if key in spec_attrs}
        if not offer_attrs:
            return None

        candidates: list[ProductVariant] = []

        for variant in variants:
            variant_values = {key: variant.attribute(key) for key in keys_to_use}

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
            missing_constrained = [
                key
                for key in optional_keys
                if key in scope.constraints and key not in spec_attrs
            ]
            if kind == MatchKind.IDENTICAL and missing_constrained:
                kind = MatchKind.SIMILAR
                notes.append(
                    "Optional spec "
                    f"({', '.join(missing_constrained)}) was not stated on the listing."
                )
            elif kind == MatchKind.SIMILAR:
                notes.append("Attributes matched a variant outside the confirmed scope.")
            return variant, kind, notes

        if len(candidates) > 1:
            return candidates[0], MatchKind.SIMILAR, ["Attribute match ambiguous across variants."]

        return None


def _title_mentions_variant_identity(title: str, variants: list[ProductVariant]) -> bool:
    """True when a family variant's model number or GTIN is written in the title."""
    folded = title.casefold()
    compact = _NON_ALNUM.sub("", folded)
    title_digits = normalize_gtin(title)
    for variant in variants:
        if variant.model_number:
            code = variant.model_number.strip().casefold()
            if code and code in folded:
                return True
            compact_code = _NON_ALNUM.sub("", code)
            if len(compact_code) >= 8 and compact_code in compact:
                return True
        if variant.gtin:
            gtin = normalize_gtin(variant.gtin)
            if gtin and gtin in title_digits:
                return True
    return False
