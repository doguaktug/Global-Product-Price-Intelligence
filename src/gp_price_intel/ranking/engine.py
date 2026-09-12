"""Weighted ranking (see docs/proposed-algorithm.md)."""

from __future__ import annotations

import re

from gp_price_intel.domain.models import (
    LandedCostCompleteness,
    Offer,
    ScoreBreakdown,
    Source,
    UserPreferences,
)
from gp_price_intel.ranking.confidence import (
    reliability_warning,
    review_volume_score,
)

_COMPLETENESS_MULTIPLIER = {
    LandedCostCompleteness.COMPLETE: 1.0,
    LandedCostCompleteness.PARTIAL: 0.9,
    LandedCostCompleteness.UNKNOWN: 0.75,
}

# How much the source's own reputation moves the seller criterion. The rest is the
# seller's own rating, so a strong seller on a mid-tier site is not capped by the site.
SOURCE_RELIABILITY_WEIGHT = 0.30

_WARRANTY_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yıl|yil)\b", re.IGNORECASE)
_WARRANTY_MONTHS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?|ay)\b", re.IGNORECASE)
_WARRANTY_DAYS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:days?|gün)\b", re.IGNORECASE)

_DELIVERY_SAME_DAY = re.compile(r"same[\s-]?day|aynı gün|today", re.IGNORECASE)
_DELIVERY_NEXT_DAY = re.compile(r"next[\s-]?day|overnight|tomorrow|ertesi gün|翌日", re.IGNORECASE)
_DELIVERY_MONTHS = re.compile(r"months?|monate|\bay\b", re.IGNORECASE)
_DELIVERY_WEEKS = re.compile(r"weeks?|wochen?|hafta|semaines?", re.IGNORECASE)
_DELIVERY_DAYS = re.compile(r"days?|werktage|tage|günü|gün|jours?|giorni|日", re.IGNORECASE)
_DELIVERY_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—~]\s*(\d+(?:[.,]\d+)?)")
_DELIVERY_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def warranty_months(warranty: str | None) -> float | None:
    """Parse a warranty string into months. Missing or unparseable → None (criterion dropped)."""
    if warranty is None:
        return None
    text = warranty.strip()
    if not text:
        return None

    years = _WARRANTY_YEARS.search(text)
    if years:
        return float(years.group(1)) * 12
    months = _WARRANTY_MONTHS.search(text)
    if months:
        return float(months.group(1))
    days = _WARRANTY_DAYS.search(text)
    if days:
        return float(days.group(1)) / 30.0
    return None


def _as_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def delivery_days(delivery_time: str | None) -> float | None:
    """
    Parse a source's own delivery phrasing into days.

    Sources write this field however they like and in their own language
    ("2-4 Werktage", "1-3 iş günü", "2-5日"), so the unit word decides the scale and
    a bare number with no unit is not treated as an estimate. Unparseable → None
    (criterion dropped for that offer).
    """
    if delivery_time is None:
        return None
    text = delivery_time.strip()
    if not text:
        return None

    if _DELIVERY_SAME_DAY.search(text):
        return 0.0
    if _DELIVERY_NEXT_DAY.search(text):
        return 1.0

    # A quoted range is a promise of its slowest end — that is what the buyer waits for.
    span = _DELIVERY_RANGE.search(text)
    if span:
        value: float | None = _as_float(span.group(2))
    else:
        found = _DELIVERY_NUMBER.search(text)
        value = _as_float(found.group(0)) if found else None
    if value is None:
        return None

    if _DELIVERY_MONTHS.search(text):
        return value * 30.0
    if _DELIVERY_WEEKS.search(text):
        return value * 7.0
    if _DELIVERY_DAYS.search(text):
        return value
    return None


def _min_max(values: list[float]) -> dict[int, float]:
    if not values:
        return {}
    low = min(values)
    high = max(values)
    if high == low:
        return {index: 1.0 for index in range(len(values))}
    return {index: (value - low) / (high - low) for index, value in enumerate(values)}


def _inverse_min_max(values: list[float]) -> dict[int, float]:
    if not values:
        return {}
    low = min(values)
    high = max(values)
    if high == low:
        return {index: 1.0 for index in range(len(values))}
    return {index: 1.0 - ((value - low) / (high - low)) for index, value in enumerate(values)}


def _scale_optional(values: list[float | None], *, inverse: bool = False) -> list[float | None]:
    present = [(index, value) for index, value in enumerate(values) if value is not None]
    scaled_out: list[float | None] = [None] * len(values)
    if not present:
        return scaled_out
    scaler = _inverse_min_max if inverse else _min_max
    scaled = scaler([value for _, value in present])
    for position, (index, _) in enumerate(present):
        scaled_out[index] = scaled[position]
    return scaled_out


def _review_signal(offer: Offer) -> float:
    if offer.seller.review_count is not None:
        return review_volume_score(offer.seller.review_count)
    if offer.seller.reliability is not None:
        return offer.seller.reliability
    return offer.data_confidence


def _price_signal(offer: Offer) -> float:
    if offer.landed_cost is not None:
        return float(offer.landed_cost.total.amount)
    if offer.converted_list_price is not None:
        return float(offer.converted_list_price.reference.amount)
    raise ValueError(
        f"Offer {offer.id} has no price in the reference currency. Offers that fail "
        "conversion are dropped before ranking, so raw amounts in mixed currencies "
        "must never reach the price criterion."
    )


def _seller_signal(offer: Offer, sources: dict[str, Source]) -> float:
    """Blend the seller's own rating with the reputation of the site hosting it."""
    source = sources.get(offer.source_id)
    seller = offer.seller.reliability
    if seller is None:
        # No seller rating at all: the site's reputation is the only trust signal left.
        return source.reliability if source is not None else offer.data_confidence
    if source is None:
        return seller
    return (1.0 - SOURCE_RELIABILITY_WEIGHT) * seller + SOURCE_RELIABILITY_WEIGHT * source.reliability


class RankingEngine:
    def score(
        self,
        offers: list[Offer],
        preferences: UserPreferences,
        sources: dict[str, Source] | None = None,
    ) -> list[tuple[Offer, ScoreBreakdown]]:
        if not offers:
            return []

        source_by_id = sources or {}
        weights = preferences.weights
        price_values = [_price_signal(offer) for offer in offers]
        seller_values = [_seller_signal(offer, source_by_id) for offer in offers]
        review_values = [_review_signal(offer) for offer in offers]
        delivery_values: list[float | None] = [
            delivery_days(offer.delivery_time) for offer in offers
        ]
        warranty_values: list[float | None] = [warranty_months(offer.warranty) for offer in offers]

        price_scores = _inverse_min_max(price_values)
        seller_scores = _min_max(seller_values)
        review_scores = _min_max(review_values)
        # Fewer days is better, so delivery is scaled in the same direction as price.
        delivery_scores = _scale_optional(delivery_values, inverse=True)
        warranty_scores = _scale_optional(warranty_values)

        scored: list[tuple[Offer, ScoreBreakdown]] = []
        for index, offer in enumerate(offers):
            criterion_scores = {
                "price": price_scores.get(index, 0.0),
                "seller": seller_scores.get(index, 0.0),
                "reviews": review_scores.get(index, 0.0),
            }
            missing: list[str] = []
            for key, optional_scores in (
                ("delivery", delivery_scores),
                ("warranty", warranty_scores),
            ):
                criterion_score = optional_scores[index]
                if criterion_score is None:
                    missing.append(key)
                else:
                    criterion_scores[key] = criterion_score

            active_weights = {
                key: weights.get(key, 0.0) for key in criterion_scores if weights.get(key, 0.0) > 0
            }
            weight_sum = sum(active_weights.values()) or 1.0
            normalized_weights = {key: value / weight_sum for key, value in active_weights.items()}

            base_score = sum(
                normalized_weights.get(key, 0.0) * criterion_scores[key] for key in criterion_scores
            )
            completeness = (
                offer.landed_cost.completeness
                if offer.landed_cost
                else LandedCostCompleteness.UNKNOWN
            )
            confidence_multiplier = offer.data_confidence * _COMPLETENESS_MULTIPLIER[completeness]
            penalty = 1.0 - confidence_multiplier
            final_score = base_score * confidence_multiplier
            breakdown = ScoreBreakdown(
                criterion_scores=criterion_scores,
                weights_used=normalized_weights,
                missing_criteria=missing,
                confidence_penalty=penalty,
                final_score=final_score,
            )
            breakdown.reliability_warning = reliability_warning(breakdown)
            scored.append((offer, breakdown))

        scored.sort(key=lambda item: item[1].final_score, reverse=True)
        return scored
