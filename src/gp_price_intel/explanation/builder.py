"""Explanation builder stub — plain-language why for highlights."""

from __future__ import annotations

from gp_price_intel.domain.models import Explanation, Offer, ScoreBreakdown


class ExplanationBuilder:
    def build(self, offer: Offer, score: ScoreBreakdown, label: str) -> Explanation:
        return Explanation(
            headline=f"{label}: explanation pending implementation",
            reasons=[],
            caveats=["Decision engine not fully wired yet."],
        )
