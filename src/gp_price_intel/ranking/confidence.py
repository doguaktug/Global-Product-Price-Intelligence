"""Offer confidence from source reputation + review signals."""

from __future__ import annotations

import math

from gp_price_intel.domain.models import ScoreBreakdown, Seller, Source

# Effective confidence (1 - confidence_penalty) must meet this to appear
# in Decision Page recommendations. Full ranked list still includes all offers.
HIGHLIGHT_MIN_CONFIDENCE = 0.7


def review_volume_score(review_count: int | None) -> float:
    """Map review/feedback volume to 0–1. Sparse reviews → lower score."""
    if review_count is None:
        return 0.55
    if review_count <= 0:
        return 0.35
    # ~1 → 0.45, ~10 → 0.60, ~100 → 0.75, ~1000 → 0.90, 10k+ → ~1.0
    return min(1.0, 0.35 + 0.20 * math.log10(review_count + 1))


def compute_data_confidence(
    *,
    source_reliability: float,
    seller_reliability: float | None = None,
    review_count: int | None = None,
) -> float:
    """
    Lesser-known sites and lightly reviewed sellers get lower confidence.

    Weights: source reputation dominates, then seller rating, then review volume.
    """
    seller = 0.5 if seller_reliability is None else seller_reliability
    reviews = review_volume_score(review_count)
    score = (
        0.45 * source_reliability
        + 0.30 * seller
        + 0.25 * reviews
    )
    return max(0.0, min(1.0, score))


def compute_data_confidence_from(
    source: Source,
    seller: Seller,
) -> float:
    return compute_data_confidence(
        source_reliability=source.reliability,
        seller_reliability=seller.reliability,
        review_count=seller.review_count,
    )


def effective_confidence(breakdown: ScoreBreakdown) -> float:
    """Confidence multiplier used in scoring (= 1 - confidence_penalty)."""
    return max(0.0, min(1.0, 1.0 - breakdown.confidence_penalty))


def is_highlight_eligible(
    breakdown: ScoreBreakdown,
    *,
    threshold: float = HIGHLIGHT_MIN_CONFIDENCE,
) -> bool:
    return effective_confidence(breakdown) >= threshold


def reliability_warning(breakdown: ScoreBreakdown) -> str | None:
    if is_highlight_eligible(breakdown):
        return None
    return (
        "Not reliable enough for a top recommendation — "
        "lesser-known source or limited review history."
    )
