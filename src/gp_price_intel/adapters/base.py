"""Source adapter contract and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod

from gp_price_intel.domain.models import Offer, SearchScope, Source


class SourceFetchError(RuntimeError):
    """
    A source could not be consulted, as opposed to having nothing to offer.

    The orchestrator keeps going on this — one dead source must not sink a search
    that other sources can still answer — but it records the reason. Returning an
    empty list instead would make "your credentials are rejected" indistinguishable
    from "this product is not listed here", which is the one distinction someone
    debugging a live adapter actually needs.
    """


class SourceAdapter(ABC):
    """
    One retailer/API/fixture per adapter.

    Return `[]` when the source answered and had nothing. Raise `SourceFetchError`
    when the source could not be reached or refused the request.
    """

    source: Source

    @abstractmethod
    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        """Fetch listings for the search scope."""

    def known_sources(self) -> list[Source]:
        """
        Every source this adapter can stamp on an offer.

        Ranking needs the `Source` behind `Offer.sourceId` to weigh site reputation,
        and an adapter may speak for more than one site.
        """
        return [self.source]

    async def enrich(self, offers: list[Offer], scope: SearchScope) -> list[Offer]:
        """
        Fetch more evidence for offers the matcher could not place. Default: no change.

        Search results are usually a summary — a title, a price, a seller. A title is
        the seller's marketing line, not a spec sheet, so "Galaxy S26 Ultra" may name
        no build at all while the listing's own item-specifics table states the storage
        and RAM exactly. Sources that publish that detail behind a second request can
        implement this to supply it, and the orchestrator re-matches whatever comes
        back.

        Called only for offers that failed to identify one variant, so the extra
        requests scale with the ambiguous listings rather than with every result.
        Best-effort by contract: return the offer unchanged rather than raising when
        the detail cannot be had, since an un-enriched offer is no worse off than it
        already was.
        """
        return offers

    def unavailable_reason(self) -> str | None:
        """
        Why this adapter cannot search at all, or `None` when it can.

        A source that is switched off contributes nothing, which looks exactly like a
        source that had nothing — so an empty Decision Page can say which sources were
        never asked instead of implying the product is unlisted everywhere.
        """
        return None
