"""Search session orchestrator — coordinates the end-to-end pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import NoReturn
from uuid import uuid4

from gp_price_intel.adapters.base import SourceAdapter, SourceFetchError
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
    Source,
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
from gp_price_intel.normalize.condition import is_non_new_condition, offer_condition_from_specs_and_title

logger = logging.getLogger(__name__)


class SearchFailed(RuntimeError):
    """Raised when a search cannot produce a Decision Page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _empty_result_reason(
    *,
    ref_currency: str,
    adapter_errors: list[str],
    fetched: int,
    out_of_stock: int,
    used_filtered: int,
    eligible: int,
    unmatched: int,
    conversion_failures: list[str],
    landed_failures: list[str],
    adapters_configured: bool,
    skipped_sources: list[str] | None = None,
) -> str:
    """Build a user-facing reason when no offers remain for a Decision Page."""
    if not adapters_configured:
        return "No offer sources are configured."
    if fetched == 0:
        if adapter_errors:
            sample = "; ".join(adapter_errors[:3])
            return f"No offers could be collected. Sources failed: {sample}."
        if skipped_sources:
            return (
                "No offers were found for this product. "
                f"{'; '.join(skipped_sources)}."
            )
        return "No offers were found for this product."
    if fetched == out_of_stock:
        return "All collected offers were out of stock."
    if fetched == out_of_stock + used_filtered and used_filtered > 0:
        return "All remaining offers were used, refurbished, or open-box. Turn on “include used” to compare them."
    if eligible == 0 and used_filtered > 0 and unmatched == 0:
        return "Only used, refurbished, or open-box listings were found. Turn on “include used” to compare them."
    if eligible == 0:
        if unmatched:
            return "None of the collected listings matched the confirmed product."
        return "No matching offers remained after filtering."
    if conversion_failures and len(conversion_failures) == eligible and not landed_failures:
        sample = conversion_failures[0]
        return (
            f"All {len(conversion_failures)} offers failed currency conversion "
            f"to {ref_currency}. {sample}"
        )
    if len(conversion_failures) + len(landed_failures) == eligible:
        parts: list[str] = []
        if conversion_failures:
            parts.append(f"{len(conversion_failures)} failed currency conversion")
        if landed_failures:
            parts.append(f"{len(landed_failures)} failed landed-cost estimation")
        sample = (landed_failures or conversion_failures)[0]
        return f"All offers were dropped ({', '.join(parts)}). {sample}"
    return "No offers remained to build a Decision Page."


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
        self.landed_cost = LandedCostService(fx=self.fx)
        self.ranking = RankingEngine()
        self.explanations = ExplanationBuilder()
        self.alternatives = AlternativeScout(catalog=self.catalog)
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
        adapter_errors: list[str] = []
        include_used = bool(session.preferences.include_used)
        raw_offers = await self._fetch_offers(
            scope, destination, adapter_errors, include_used=include_used
        )
        fetched = len(raw_offers)
        in_stock = [
            offer for offer in raw_offers if offer.stock_status != StockStatus.OUT_OF_STOCK
        ]
        out_of_stock = fetched - len(in_stock)

        # Optionally drop used / refurbished / open-box (default: new only).
        kept: list = []
        used_filtered = 0
        for offer in in_stock:
            condition = offer_condition_from_specs_and_title(offer)
            if offer.condition != condition:
                offer = offer.model_copy(update={"condition": condition})
            if not include_used and is_non_new_condition(condition):
                used_filtered += 1
                continue
            kept.append(offer)

        matched = self.matcher.match(kept, scope)
        eligible = [offer for offer in matched if offer.match_kind != MatchKind.UNMATCHED]
        unmatched = len(matched) - len(eligible)

        family = self.catalog.get_family(scope.family_id)
        if family is None:
            # Duty, shipping and registration all key off the category — guessing one
            # would price a laptop as a handset.
            self._fail(session, f"Unknown product family {scope.family_id!r} in this search.")
        category_id = family.category_id

        conversion_failures: list[str] = []
        landed_failures: list[str] = []
        enriched: list = []
        for offer in eligible:
            try:
                converted = await self.fx.convert(offer.list_price, ref_currency)
            except Exception as exc:
                logger.exception(
                    "Dropping offer %s after FX conversion failure (%s→%s)",
                    offer.id,
                    offer.list_price.currency,
                    ref_currency,
                )
                conversion_failures.append(
                    f"{offer.list_price.currency}→{ref_currency}: {exc}"
                )
                continue
            offer = offer.model_copy(update={"converted_list_price": converted})
            try:
                landed = await self.landed_cost.estimate(
                    converted,
                    offer.country,
                    destination,
                    category_id,
                )
            except Exception as exc:
                logger.exception(
                    "Dropping offer %s after landed-cost failure",
                    offer.id,
                )
                landed_failures.append(str(exc))
                continue
            enriched.append(offer.model_copy(update={"landed_cost": landed}))

        if not enriched:
            self._fail(
                session,
                _empty_result_reason(
                    ref_currency=ref_currency,
                    adapter_errors=adapter_errors,
                    fetched=fetched,
                    out_of_stock=out_of_stock,
                    used_filtered=used_filtered,
                    eligible=len(eligible),
                    unmatched=unmatched,
                    conversion_failures=conversion_failures,
                    landed_failures=landed_failures,
                    adapters_configured=bool(self.adapters),
                    skipped_sources=self._skipped_sources(),
                ),
            )

        # Confirmed builds and near-offers are scored in ONE normalization pass, then
        # split. Min–max scaling is relative to the set it is given, so scoring
        # alternatives separately would produce numbers that cannot be compared to
        # the ranked list — and the rival test is exactly such a comparison.
        identical = [offer for offer in enriched if offer.match_kind == MatchKind.IDENTICAL]
        ranked = self.ranking.score(enriched, session.preferences, self._source_registry())

        if identical:
            confirmed_scored = [
                item for item in ranked if item[0].match_kind == MatchKind.IDENTICAL
            ]
            near_scored = [item for item in ranked if item[0].match_kind != MatchKind.IDENTICAL]
        else:
            # Nothing matched the confirmed build exactly, so there is no "confirmed
            # build vs its variants" split to make. Rank what we have.
            confirmed_scored = ranked
            near_scored = []

        # Peers are the pre-explanation breakdowns, which carry the same scores and
        # weights. The set includes the offer being explained: the builder locates it
        # to find the offer directly above it, which is the comparison it has to answer.
        scored = [
            (
                offer,
                breakdown.model_copy(
                    update={
                        "explanation": self.explanations.build(
                            offer, breakdown, "Ranked offer", confirmed_scored
                        ),
                        "reliability_warning": breakdown.reliability_warning,
                    }
                ),
            )
            for offer, breakdown in confirmed_scored
        ]
        highlights = pick_highlights(scored, session.preferences, self.explanations)

        variant_id = confirmed_variant_id or session.confirmed_variant_id
        confirmed_variant = self.catalog.get_variant(variant_id) if variant_id else None

        best = scored[0] if scored else None
        alt_list = self.alternatives.select(near_scored, best, confirmed_variant)
        near_by_id = {offer.id: offer for offer, _ in near_scored}
        alternative_offers = [
            near_by_id[alt.offer_id] for alt in alt_list if alt.offer_id in near_by_id
        ]

        session.status = SessionStatus.RANKED
        session.failure_reason = None
        return DecisionPage(
            session_id=session.id,
            confirmed_variant=confirmed_variant,
            offers=[offer for offer, _ in scored],
            offer_scores={offer.id: breakdown for offer, breakdown in scored},
            highlights=highlights,
            alternatives=alt_list,
            alternative_offers=alternative_offers,
            generated_at=datetime.now(timezone.utc),
        )

    def _skipped_sources(self) -> list[str]:
        """Sources that could not be searched at all, so an empty page can say so."""
        reasons = [adapter.unavailable_reason() for adapter in self.adapters]
        return [reason for reason in reasons if reason]

    def _source_registry(self) -> dict[str, Source]:
        """Map `Offer.sourceId` back to the source, so ranking can weigh site reputation."""
        return {
            source.id: source
            for adapter in self.adapters
            for source in adapter.known_sources()
        }

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

    def _fail(self, session: SearchSession, reason: str) -> NoReturn:
        session.status = SessionStatus.FAILED
        session.failure_reason = reason
        logger.error("Search %s failed: %s", session.id, reason)
        raise SearchFailed(reason)

    async def _fetch_offers(
        self,
        scope: SearchScope,
        destination_country: str,
        errors: list[str] | None = None,
        *,
        include_used: bool = False,
    ) -> list:
        collected_errors = errors if errors is not None else []
        if not self.adapters:
            return []

        results = await asyncio.gather(
            *[
                adapter.search(scope, destination_country, include_used=include_used)
                for adapter in self.adapters
            ],
            return_exceptions=True,
        )

        offers = []
        for adapter, result in zip(self.adapters, results, strict=True):
            if isinstance(result, Exception):
                source = getattr(adapter, "source", None)
                label = getattr(source, "id", None) or type(adapter).__name__
                if isinstance(result, SourceFetchError):
                    # Anticipated: the source was reachable-but-refused, or offline.
                    # The message is the useful part, so it is logged without a stack.
                    logger.warning("Source %s could not be searched: %s", label, result)
                else:
                    logger.exception("Adapter %s failed", label, exc_info=result)
                collected_errors.append(f"{label}: {result}")
                continue
            offers.extend(result)
        return offers

    def preview_normalization(self, raw_query: str) -> NormalizedQuery:
        return self.normalizer.normalize(raw_query)
