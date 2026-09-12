"""Re-ranking reuses a remembered fetch instead of asking the retailers again."""

from __future__ import annotations

import pytest

from gp_price_intel.adapters.base import SourceAdapter
from gp_price_intel.domain.models import SearchScope, UserPreferences
from gp_price_intel.orchestrator.search import SearchOrchestrator
from gp_price_intel.orchestrator.search_memory import (
    MAX_REMEMBERED_SEARCHES,
    RememberedSearch,
    SearchExpired,
    SearchMemory,
)

from tests.test_pipeline import pipeline_orchestrator  # noqa: F401  (pytest fixture)

QUERY = "Samsung Galaxy S26 Ultra 512 GB Black"
TR_TRY = UserPreferences(destination_country="TR", reference_currency="TRY")


class _CountingAdapter(SourceAdapter):
    """Wraps a real adapter and counts how many times it is asked to fetch."""

    def __init__(self, inner: SourceAdapter) -> None:
        self.inner = inner
        self.source = inner.source
        self.calls = 0

    async def search(self, scope: SearchScope, destination_country: str) -> list:
        self.calls += 1
        return await self.inner.search(scope, destination_country)

    def known_sources(self) -> list:
        return self.inner.known_sources()


def _price_heavy() -> UserPreferences:
    return UserPreferences(
        destination_country="TR",
        reference_currency="TRY",
        weights={"price": 1.0, "seller": 0.0, "reviews": 0.0, "delivery": 0.0, "warranty": 0.0},
    )


def _warranty_heavy() -> UserPreferences:
    return UserPreferences(
        destination_country="TR",
        reference_currency="TRY",
        weights={"price": 0.0, "seller": 0.0, "reviews": 0.0, "delivery": 0.0, "warranty": 1.0},
    )


@pytest.mark.asyncio
async def test_rerank_does_not_fetch_again(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """The whole point: moving a slider must not re-hit a single retailer."""
    counting = _CountingAdapter(pipeline_orchestrator.adapters[0])
    pipeline_orchestrator.adapters = [counting]

    session = pipeline_orchestrator.start_session(QUERY, _price_heavy())
    await pipeline_orchestrator.run(session)
    assert counting.calls == 1

    await pipeline_orchestrator.rerank(session, _warranty_heavy())
    await pipeline_orchestrator.rerank(session, _price_heavy())

    assert counting.calls == 1


@pytest.mark.asyncio
async def test_rerank_actually_changes_the_recommendation(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """
    A re-rank that never changes anything would be a no-op dressed as a feature.

    Cheapest-wins and warranty-wins must be able to disagree on the ordering.
    """
    session = pipeline_orchestrator.start_session(QUERY, _price_heavy())
    on_price = await pipeline_orchestrator.run(session)
    on_warranty = await pipeline_orchestrator.rerank(session, _warranty_heavy())

    assert [o.id for o in on_price.offers] != [o.id for o in on_warranty.offers]

    cheapest = min(on_price.offers, key=lambda o: o.landed_cost.total.amount)
    assert on_price.offers[0].id == cheapest.id


@pytest.mark.asyncio
async def test_rerank_compares_the_same_offers(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """Same set, reordered — a re-rank must not quietly add or drop a listing."""
    session = pipeline_orchestrator.start_session(QUERY, _price_heavy())
    first = await pipeline_orchestrator.run(session)
    second = await pipeline_orchestrator.rerank(session, _warranty_heavy())

    assert {o.id for o in first.offers} == {o.id for o in second.offers}
    for before, after in zip(
        sorted(first.offers, key=lambda o: o.id), sorted(second.offers, key=lambda o: o.id)
    ):
        assert before.list_price == after.list_price
        assert before.landed_cost == after.landed_cost
        assert before.collected_at == after.collected_at


@pytest.mark.asyncio
async def test_rerank_refuses_to_change_destination_or_currency(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """
    Landed cost and FX were computed against the old destination and currency.

    Honouring a change here would present totals that answer a different question,
    so the caller is told to run the search again instead.
    """
    session = pipeline_orchestrator.start_session(QUERY, TR_TRY)
    await pipeline_orchestrator.run(session)

    with pytest.raises(SearchExpired, match="landed cost"):
        await pipeline_orchestrator.rerank(
            session, UserPreferences(destination_country="DE", reference_currency="TRY")
        )
    with pytest.raises(SearchExpired, match="landed cost"):
        await pipeline_orchestrator.rerank(
            session, UserPreferences(destination_country="TR", reference_currency="EUR")
        )


@pytest.mark.asyncio
async def test_rerank_without_a_remembered_search_is_refused(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    session = pipeline_orchestrator.start_session(QUERY, TR_TRY)

    with pytest.raises(SearchExpired, match="no longer in memory"):
        await pipeline_orchestrator.rerank(session, _warranty_heavy())


@pytest.mark.asyncio
async def test_an_expired_search_is_refused_rather_than_served_stale(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """A stale price is worse than no answer, so expiry sends the client back to run."""
    pipeline_orchestrator.memory = SearchMemory(ttl_seconds=0)
    session = pipeline_orchestrator.start_session(QUERY, TR_TRY)
    await pipeline_orchestrator.run(session)

    with pytest.raises(SearchExpired, match="no longer in memory"):
        await pipeline_orchestrator.rerank(session, _warranty_heavy())


@pytest.mark.asyncio
async def test_run_never_reads_the_memory(
    pipeline_orchestrator: SearchOrchestrator,  # noqa: F811
) -> None:
    """
    The memory is a cache, not a session store.

    /search/run must work identically with it emptied, because nothing about
    answering a search is allowed to depend on what happened to be remembered.
    """
    session = pipeline_orchestrator.start_session(QUERY, _price_heavy())
    first = await pipeline_orchestrator.run(session)

    pipeline_orchestrator.memory.forget(session.id)
    second = await pipeline_orchestrator.run(session)

    assert [o.id for o in first.offers] == [o.id for o in second.offers]


# --- SearchMemory in isolation ------------------------------------------------


def _search(offer_count: int = 0) -> RememberedSearch:
    return RememberedSearch(
        offers=[],
        confirmed_variant_id=f"variant-{offer_count}",
        destination_country="TR",
        reference_currency="TRY",
    )


def test_memory_returns_what_it_was_given() -> None:
    memory = SearchMemory(ttl_seconds=600)
    memory.remember("s1", _search())

    recalled = memory.recall("s1")
    assert recalled is not None
    assert recalled.destination_country == "TR"


def test_memory_misses_are_not_errors() -> None:
    assert SearchMemory(ttl_seconds=600).recall("never-stored") is None


def test_a_zero_ttl_disables_the_memory_entirely() -> None:
    """Setting the TTL to 0 is the switch for "do not remember anything"."""
    memory = SearchMemory(ttl_seconds=0)
    memory.remember("s1", _search())

    assert memory.recall("s1") is None


def test_memory_is_bounded_and_evicts_the_oldest() -> None:
    """An unbounded cache is a memory leak with extra steps."""
    memory = SearchMemory(ttl_seconds=600)
    for index in range(MAX_REMEMBERED_SEARCHES + 5):
        memory.remember(f"s{index}", _search(index))

    assert memory.recall("s0") is None
    assert memory.recall(f"s{MAX_REMEMBERED_SEARCHES + 4}") is not None


def test_re_remembering_a_session_replaces_it_without_evicting_others() -> None:
    memory = SearchMemory(ttl_seconds=600)
    for index in range(MAX_REMEMBERED_SEARCHES):
        memory.remember(f"s{index}", _search(index))

    memory.remember("s0", _search(999))

    recalled = memory.recall("s0")
    assert recalled is not None
    assert recalled.confirmed_variant_id == "variant-999"
    assert memory.recall("s1") is not None