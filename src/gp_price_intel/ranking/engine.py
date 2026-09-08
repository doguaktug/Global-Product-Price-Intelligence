"""Weighted ranking (see docs/proposed-algorithm.md)."""

from __future__ import annotations

import re

from gp_price_intel.domain.models import (
    LandedCostCompleteness,
    Offer,
    ScoreBreakdown,
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

_WARRANTY_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?|yıl|yil)\b", re.IGNORECASE)
_WARRANTY_MONTHS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:months?|mos?|ay)\b", re.IGNORECASE)
_WARRANTY_DAYS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:days?|gün)\b", re.IGNORECASE)


def warranty_months(warranty: str | None) -> float | None:
    """Parse a warranty string into months. Missing/blank → None (criterion dropped)."""
    if warranty is None:
        return None
    text = warranty.strip()
    if not text:
        return None

    years = _WARRANTY_YEARS.search(text)
    months = _WARRANTY_MONTHS.search(text)
    days = _WARRANTY_DAYS.search(text)
    value: float | None = None
    if years:
        value = float(years.group(1)) * 12
    elif months:
        value = float(months.group(1))
    elif days:
        value = float(days.group(1)) / 30.0
    else:
        value = 6.0

    if re.search(r"\b(manufacturer|official|applecare)\b", text, re.IGNORECASE):
        value += 6.0
    return value


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


class RankingEngine:
    def score(
        self,
        offers: list[Offer],
        preferences: UserPreferences,
    ) -> list[tuple[Offer, ScoreBreakdown]]:
        if not offers:
            return []

        weights = preferences.weights
        price_values = [
            float(offer.landed_cost.total.amount)
            if offer.landed_cost
            else float(offer.converted_list_price.reference.amount)
            if offer.converted_list_price
            else float(offer.list_price.amount)
            for offer in offers
        ]
        seller_values = [offer.seller.reliability or offer.data_confidence for offer in offers]
        review_values = [_review_signal(offer) for offer in offers]
        delivery_values = [1.0 if offer.delivery_time else 0.5 for offer in offers]
        warranty_values: list[float | None] = [warranty_months(offer.warranty) for offer in offers]

        price_scores = _inverse_min_max(price_values)
        seller_scores = _min_max(seller_values)
        review_scores = _min_max(review_values)
        delivery_scores = _min_max(delivery_values)
        warranty_scores = _scale_optional(warranty_values)

        scored: list[tuple[Offer, ScoreBreakdown]] = []
        for index, offer in enumerate(offers):
            criterion_scores = {
                "price": price_scores.get(index, 0.0),
                "seller": seller_scores.get(index, 0.0),
                "reviews": review_scores.get(index, 0.0),
                "delivery": delivery_scores.get(index, 0.0),
            }
            missing: list[str] = []
            warranty_score = warranty_scores[index]
            if warranty_score is None:
                missing.append("warranty")
            else:
                criterion_scores["warranty"] = warranty_score

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
