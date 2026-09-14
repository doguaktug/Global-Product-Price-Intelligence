"""
Check a live source adapter from the command line.

    python -m gp_price_intel.diagnose "Samsung Galaxy S26 512GB"

The Decision Page can only say that no offers survived; it cannot say whether eBay
refused the credentials, answered with nothing, or answered with listings that were
then dropped as unmatched. This walks those stages in order and prints where the
listings stop, which is the question someone wiring up real keys is actually asking.

Credential *values* are never printed — only whether they are set.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from gp_price_intel.adapters.base import SourceFetchError
from gp_price_intel.adapters.ebay import EbayAdapter
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import get_settings
from gp_price_intel.domain.models import MatchKind, SearchScope
from gp_price_intel.matching.matcher import ProductMatcher

DEFAULT_QUERY = "Samsung Galaxy S26 512GB"

# Query keys that describe a build. The rest of `extracted` is bookkeeping (match
# scores, family labels) and does not belong in a search scope.
_SPEC_KEYS = (
    "storage_gb",
    "memory_gb",
    "processor",
    "connectivity",
    "colour",
    "region_version",
)


def _scope_for(query: str, catalog: CatalogRepository) -> SearchScope | None:
    """
    Build a search scope without the confirmation gate.

    A real search refuses to run until the product is unambiguous. That is right for
    users and wrong here: the point is to reach eBay, so an under-specified query is
    widened to the family rather than turned into a popup.
    """
    from gp_price_intel.normalize.query_normalizer import QueryNormalizer

    normalized = QueryNormalizer(catalog).normalize(query)
    if normalized.candidate_family_id is None:
        return None
    return SearchScope(
        family_id=normalized.candidate_family_id,
        constraints={
            key: value
            for key, value in normalized.extracted.items()
            if key in _SPEC_KEYS and value is not None
        },
        variant_ids=list(normalized.candidate_variant_ids),
    )


async def diagnose_ebay(query: str, destination: str) -> int:
    settings = get_settings()
    catalog = CatalogRepository()

    env_path = Path(".env").resolve()
    print(f"cwd            {Path.cwd()}")
    print(f".env           {env_path} ({'found' if env_path.exists() else 'MISSING'})")
    print(f"EBAY_APP_ID    {'set' if settings.ebay_app_id else 'NOT SET'}")
    print(f"EBAY_CERT_ID   {'set' if settings.ebay_cert_id else 'NOT SET'}")
    print(f"EBAY_SANDBOX   {settings.ebay_sandbox}")

    adapter = EbayAdapter(catalog=catalog, settings=settings)
    reason = adapter.unavailable_reason()
    if reason:
        print(f"\nRESULT         {reason}")
        print("Put both keys in .env, then run this again from the project root.")
        return 1

    scope = _scope_for(query, catalog)
    if scope is None:
        print(f"\nRESULT         {query!r} matched no catalog family, so no search was built.")
        return 1

    print(f"\nfamily         {scope.family_id}")
    print(f"constraints    {scope.constraints or '{}'}")
    print(f"eBay query     {adapter.build_search_query(scope)!r}")

    try:
        offers = await adapter.search(scope, destination)
    except SourceFetchError as exc:
        print(f"\nRESULT         eBay call FAILED\n               {exc}")
        return 1
    finally:
        await adapter.aclose()

    print(f"\nlistings kept  {len(offers)}")
    if not offers:
        print(
            "RESULT         eBay answered, but no usable listing came back. Either it "
            "lists nothing\n               for this query, or every listing was missing "
            "a price, URL or stock."
        )
        return 1

    matched = ProductMatcher(catalog).match(offers, scope)
    usable = [offer for offer in matched if offer.match_kind != MatchKind.UNMATCHED]
    for offer in matched:
        print(
            f"  [{offer.match_kind.value:>9}] "
            f"{offer.list_price.amount} {offer.list_price.currency}  {offer.listing_title[:70]}"
        )

    print(f"\nRESULT         eBay works. {len(usable)}/{len(matched)} listings matched the catalog.")
    if not usable:
        print(
            "               All of them were unmatched, so a search would still show "
            "nothing.\n               eBay titles often omit the specs the matcher needs."
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the live eBay adapter.")
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument("--destination", default=None, help="Destination country (default from .env)")
    args = parser.parse_args()
    destination = args.destination or get_settings().default_destination_country
    return asyncio.run(diagnose_ebay(args.query, destination))


if __name__ == "__main__":
    raise SystemExit(main())
