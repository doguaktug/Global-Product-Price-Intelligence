"""Search session orchestrator — coordinates the end-to-end pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.adapters.fixture import FixtureAdapter
from gp_price_intel.alternatives.scout import AlternativeScout
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    DecisionPage,
    NormalizedQuery,
    PropertyChoice,
    SearchSession,
    SessionStatus,
    UserPreferences,
)
from gp_price_intel.explanation.builder import ExplanationBuilder
from gp_price_intel.fx.service import FxService
from gp_price_intel.landed_cost.service import LandedCostService
from gp_price_intel.matching.matcher import ProductMatcher
from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.ranking.engine import RankingEngine


class SearchOrchestrator:
    """Owns one search session: normalize → confirm → fetch → decide."""

    def __init__(
        self,
        catalog: CatalogRepository | None = None,
        adapters: list[SourceAdapter] | None = None,
    ) -> None:
        self.catalog = catalog or CatalogRepository()
        self.normalizer = QueryNormalizer(self.catalog)
        self.matcher = ProductMatcher()
        self.fx = FxService()
        self.landed_cost = LandedCostService()
        self.ranking = RankingEngine()
        self.explanations = ExplanationBuilder()
        self.alternatives = AlternativeScout()
        self.adapters = adapters or [FixtureAdapter()]

    def start_session(
        self,
        raw_query: str,
        preferences: UserPreferences | None = None,
    ) -> SearchSession:
        prefs = preferences or UserPreferences()
        normalized = self.normalizer.normalize(raw_query)
        status = (
            SessionStatus.NEEDS_CONFIRMATION
            if normalized.needs_confirmation
            else SessionStatus.RECEIVED
        )
        return SearchSession(
            id=str(uuid4()),
            raw_query=raw_query,
            normalized_query=normalized,
            preferences=prefs,
            status=status,
            created_at=datetime.now(timezone.utc),
        )

    def apply_choices(
        self,
        session: SearchSession,
        choices: list[PropertyChoice],
    ) -> SearchSession:
        session.property_choices = choices
        # Week 2: build SearchScope from choices + catalog variants.
        session.status = SessionStatus.RECEIVED
        return session

    async def run(self, session: SearchSession) -> DecisionPage:
        """Full pipeline placeholder — returns an empty Decision Page for now."""
        session.status = SessionStatus.FETCHING
        # Week 2: adapters → match → FX → landed cost → rank → explain → alternatives.
        session.status = SessionStatus.RANKED
        return DecisionPage(
            session_id=session.id,
            confirmed_variant=None,
            offers=[],
            highlights=[],
            alternatives=[],
            generated_at=datetime.now(timezone.utc),
        )

    def preview_normalization(self, raw_query: str) -> NormalizedQuery:
        return self.normalizer.normalize(raw_query)
