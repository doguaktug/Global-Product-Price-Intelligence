"""Short-lived memory of a completed fetch, so weights can change without re-fetching."""

from __future__ import annotations

import time
from dataclasses import dataclass

from gp_price_intel.config import get_settings
from gp_price_intel.domain.models import Offer

#: Upper bound on remembered searches. A prototype serving a handful of users does not
#: need more, and a bound means a burst of traffic cannot grow this without limit.
MAX_REMEMBERED_SEARCHES = 128


class SearchExpired(RuntimeError):
    """The fetch behind this session is gone or no longer valid to reuse."""


@dataclass(frozen=True)
class RememberedSearch:
    """
    Everything a re-rank may reuse, and the inputs that made it.

    `destination_country` and `reference_currency` are kept because they are *not*
    reusable: landed cost and FX were computed against them, so a re-rank that
    changed either would be ranking numbers that answer a different question.
    """

    offers: list[Offer]
    confirmed_variant_id: str | None
    destination_country: str
    reference_currency: str


class SearchMemory:
    """
    A TTL cache of fetched offers, keyed by session id.

    This is a cache in front of the adapters, not a session store: the session
    itself still travels with the client, and a miss here is an ordinary outcome
    that costs a re-fetch rather than an error state. Nothing here is required to
    answer a request — `POST /search/run` never reads it.

    The point is that re-ranking is pure. Given the same offers, changing the
    weights changes only the arithmetic, so re-hitting every retailer to answer
    "what if I cared more about warranty?" would be waste — and worse than waste,
    since prices can move between the two fetches and the user would be comparing
    two different sets of offers while believing they had changed one slider.
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else get_settings().offer_cache_ttl_seconds
        )
        self._entries: dict[str, tuple[float, RememberedSearch]] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def remember(self, session_id: str, search: RememberedSearch) -> None:
        if self._ttl <= 0:
            return
        self._prune()
        if (
            len(self._entries) >= MAX_REMEMBERED_SEARCHES
            and session_id not in self._entries
        ):
            oldest = min(self._entries, key=lambda key: self._entries[key][0])
            del self._entries[oldest]
        self._entries[session_id] = (self._now(), search)

    def recall(self, session_id: str) -> RememberedSearch | None:
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        stored_at, search = entry
        if self._is_expired(stored_at):
            del self._entries[session_id]
            return None
        return search

    def forget(self, session_id: str) -> None:
        self._entries.pop(session_id, None)

    def _prune(self) -> None:
        for session_id in [
            key for key, (stored_at, _) in self._entries.items() if self._is_expired(stored_at)
        ]:
            del self._entries[session_id]

    def _is_expired(self, stored_at: float) -> bool:
        return self._now() - stored_at >= self._ttl

    @staticmethod
    def _now() -> float:
        # Monotonic, not wall clock: a clock adjustment must not make a fresh fetch
        # look hours old, or an expired one look current.
        return time.monotonic()
