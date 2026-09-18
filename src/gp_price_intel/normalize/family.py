"""Family labels and distinctive-token scoring, shared by query and offer matching."""

from __future__ import annotations

from collections.abc import Iterable

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import ProductFamily
from gp_price_intel.normalize.similarity import (
    DistinctiveVocabulary,
    build_distinctive_vocabulary,
    score_query_against_labels,
)

FAMILY_MATCH_THRESHOLD = 0.45
FAMILY_AMBIGUITY_GAP = 0.06


def family_labels(family: ProductFamily) -> list[str]:
    return [f"{family.brand} {family.family_name}", family.family_name, *family.aliases]


def catalog_vocabulary(
    families: Iterable[ProductFamily] | CatalogRepository,
) -> DistinctiveVocabulary:
    if isinstance(families, CatalogRepository):
        families = families.list_families()
    return build_distinctive_vocabulary(
        (family.brand, family_labels(family)) for family in families
    )


def sibling_family_outscores(
    text: str,
    family: ProductFamily,
    families: Iterable[ProductFamily],
    vocabulary: DistinctiveVocabulary,
) -> bool:
    """
    True when a same-brand sibling uniquely beats ``family`` on this text.

    Used on listing titles so an S26+ does not attribute-match an Ultra SKU, and
    on queries so Ultra vs Plus stay separable. ``FAMILY_MATCH_THRESHOLD`` is the
    floor for treating the rival as a real name hit; ``FAMILY_AMBIGUITY_GAP`` is
    the margin that makes it unambiguous.
    """
    confirmed = score_query_against_labels(text, family_labels(family), vocabulary)
    rival = 0.0
    for other in families:
        if other.id == family.id:
            continue
        if other.brand.casefold() != family.brand.casefold():
            continue
        score = score_query_against_labels(text, family_labels(other), vocabulary).score
        rival = max(rival, score)
    return (
        rival >= FAMILY_MATCH_THRESHOLD
        and (rival - confirmed.score) >= FAMILY_AMBIGUITY_GAP
    )
