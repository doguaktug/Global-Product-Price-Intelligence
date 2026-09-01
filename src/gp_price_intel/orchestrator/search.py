"""Search session orchestrator — coordinates the end-to-end pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.adapters.registry import build_adapters
from gp_price_intel.alternatives.scout import AlternativeScout
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    DecisionPage,
    MatchKind,
    NormalizedQuery,
    PropertyChoice,
    SearchScope,
    SearchSession,
    SessionStatus,
    StockStatus,
    UserPreferences,
)
from gp_price_intel.explanation.builder import ExplanationBuilder
from gp_price_intel.fx.service import FxService
from gp_price_intel.landed_cost.service import LandedCostService
from gp_price_intel.matching.matcher import ProductMatcher
from gp_price_intel.normalize.confirmation import ConfirmationError, resolve_search_scope
from gp_price_intel.normalize.query_normalizer import QueryNormalizer
from gp_price_intel.ranking.engine import RankingEngine
from gp_price_intel.ranking.highlights import pick_highlights

logger = logging.getLogger(__name__)


class SearchOrchestrator:
    """Owns one search session: normalize → confirm → fetch → decide."""

    def __init__(
        self,
        catalog: CatalogRepository | None = None,
        adapters: list[SourceAdapter] | None = None,
        fx: FxService | None = None,
    ) -> None:
        self.catalog = catalog or CatalogRepository()
        self.normalizer = QueryNormalizer(self.catalog)
        self.matcher = ProductMatcher(self.catalog)
        self.fx = fx or FxService()
        self.landed_cost = LandedCostService()
        self.ranking = RankingEngine()
        self.explanations = ExplanationBuilder()
        self.alternatives = AlternativeScout()
        self.adapters = adapters if adapters is not None else build_adapters(self.catalog)

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
        if session.normalized_query is None:
            raise ConfirmationError("Session has no normalized query.")

        session.property_choices = choices
        try:
            scope, confirmed_variant_id = resolve_search_scope(
                self.catalog,
                session.normalized_query,
                choices,
            )
        except ConfirmationError:
            session.status = SessionStatus.NEEDS_CONFIRMATION
            raise

        session.search_scope = scope
        session.confirmed_variant_id = confirmed_variant_id
        session.status = SessionStatus.RECEIVED
        return session

    async def run(self, session: SearchSession) -> DecisionPage:
        """Normalize → fetch → match → FX → landed cost → rank → explain."""
        scope, confirmed_variant_id = self._ensure_scope(session)
        destination = session.preferences.destination_country
        ref_currency = session.preferences.reference_currency

        session.status = SessionStatus.FETCHING
        raw_offers = await self._fetch_offers(scope, destination)
        raw_offers = [
            offer
            for offer in raw_offers
            if offer.stock_status != StockStatus.OUT_OF_STOCK
        ]

        matched = self.matcher.match(raw_offers, scope)
        eligible = [offer for offer in matched if offer.match_kind != MatchKind.UNMATCHED]

        family = self.catalog.get_family(scope.family_id)
        category_id = family.category_id if family else "smartphone"

        enriched: list = []
        for offer in eligible:
            converted = await self.fx.convert(offer.list_price, ref_currency)
            offer = offer.model_copy(update={"converted_list_price": converted})
            landed = await self.landed_cost.estimate(
                converted,
                offer.country,
                destination,
                category_id,
            )
            enriched.append(offer.model_copy(update={"landed_cost": landed}))

        scored = self.ranking.score(enriched, session.preferences)
        highlights = pick_highlights(scored, session.preferences, self.explanations)

        best_offer = scored[0][0] if scored else None
        near_offers = [offer for offer in enriched if offer.match_kind != MatchKind.IDENTICAL]
        alt_list = self.alternatives.select(near_offers, best_offer)

        variant_id = confirmed_variant_id or session.confirmed_variant_id
        confirmed_variant = self.catalog.get_variant(variant_id) if variant_id else None

        session.status = SessionStatus.RANKED
        return DecisionPage(
            session_id=session.id,
            confirmed_variant=confirmed_variant,
            offers=[offer for offer, _ in scored],
            highlights=highlights,
            alternatives=alt_list,
            generated_at=datetime.now(timezone.utc),
        )

    def _ensure_scope(self, session: SearchSession) -> tuple[SearchScope, str | None]:
        if session.search_scope is not None:
            return session.search_scope, session.confirmed_variant_id

        if session.normalized_query is None:
            raise ConfirmationError("Session has no normalized query.")

        if session.normalized_query.needs_confirmation:
            raise ConfirmationError("Session still needs confirmation before live search.")

        return resolve_search_scope(
            self.catalog,
            session.normalized_query,
            session.property_choices,
        )

    async def _fetch_offers(self, scope: SearchScope, destination_country: str) -> list:
        if not self.adapters:
            return []

        results = await asyncio.gather(
            *[adapter.search(scope, destination_country) for adapter in self.adapters],
            return_exceptions=True,
        )

        offers = []
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Adapter failed", exc_info=result)
                continue
            offers.extend(result)
        return offers

    def preview_normalization(self, raw_query: str) -> NormalizedQuery:
        return self.normalizer.normalize(raw_query)
