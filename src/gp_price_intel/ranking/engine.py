"""Weighted ranking engine stub (see docs/proposed-algorithm.md)."""

from __future__ import annotations

from gp_price_intel.domain.models import Offer, ScoreBreakdown, UserPreferences


class RankingEngine:
    def score(self, offers: list[Offer], preferences: UserPreferences) -> list[tuple[Offer, ScoreBreakdown]]:
        # Week 2: min-max normalize, re-weight missing criteria, confidence penalty.
        return [(offer, ScoreBreakdown()) for offer in offers]
