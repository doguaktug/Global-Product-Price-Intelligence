"""eBay Browse API adapter."""

from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from gp_price_intel.adapters.base import SourceAdapter, SourceFetchError
from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.config import Settings, get_settings
from gp_price_intel.domain.models import (
    AcquisitionMethod,
    Money,
    NormalizedSpec,
    Offer,
    SearchScope,
    Seller,
    Source,
    SourceKind,
    StockStatus,
)
from gp_price_intel.normalize.attribute_parser import parse_listing_attributes
from gp_price_intel.normalize.spec_parser import parse_spec_value
from gp_price_intel.ranking.confidence import compute_data_confidence_from

logger = logging.getLogger(__name__)

EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE_COUNTRY = {
    "EBAY_US": "US",
    "EBAY_GB": "GB",
    "EBAY_DE": "DE",
    "EBAY_AU": "AU",
}

# Constraint keys worth putting in the keyword query, in the order buyers write them.
_QUERY_TERMS: tuple[tuple[str, str], ...] = (
    ("processor", "{}"),
    ("storage_gb", "{}GB"),
    ("memory_gb", "{}GB RAM"),
    ("connectivity", "{}"),
)

# Seller-filled aspect names worth reading, mapped to catalog spec keys. eBay lets
# sellers name aspects freely, so this covers the common spellings rather than all.
ASPECT_SPEC_KEYS: dict[str, str] = {
    "storage capacity": "storage_gb",
    "internal storage capacity": "storage_gb",
    "ram": "memory_gb",
    "ram size": "memory_gb",
    "processor": "processor",
    "chipset/cpu model": "processor",
    "battery capacity": "battery_mah",
    "screen size": "display_inch",
    "display size": "display_inch",
    "colour": "colour",
    "color": "colour",
    "farbe": "colour",
    "renk": "colour",
    "色": "colour",
}

# Aspect names carrying a cross-retailer identifier, which beats any parsed spec.
_GTIN_ASPECTS = frozenset({"gtin", "ean", "upc"})
_MPN_ASPECTS = frozenset({"mpn", "manufacturer part number"})

# Detail lookups are one request per unplaced listing, so the search stays bounded
# when a keyword returns twenty vague titles.
MAX_DETAIL_LOOKUPS = 10


def _first_aspect_value(item: dict[str, Any], names: frozenset[str]) -> str | None:
    for aspect in item.get("localizedAspects") or []:
        if str(aspect.get("name", "")).casefold() not in names:
            continue
        values = aspect.get("value")
        if isinstance(values, list) and values:
            return str(values[0])
        if isinstance(values, str) and values:
            return values
    return None


def _detail_identifiers(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """`(gtin, mpn)` from a `getItem` payload, checking both the item and its product."""
    product = item.get("product") or {}
    gtins = product.get("gtins") or []
    mpns = product.get("mpns") or []
    gtin = (
        item.get("gtin")
        or (str(gtins[0]) if gtins else None)
        or _first_aspect_value(item, _GTIN_ASPECTS)
    )
    mpn = (
        item.get("mpn")
        or (str(mpns[0]) if mpns else None)
        or _first_aspect_value(item, _MPN_ASPECTS)
    )
    return (str(gtin) if gtin else None, str(mpn) if mpn else None)


def _already_in(term: str, parts: list[str]) -> bool:
    """True when `term` is already a whole word in the query built so far."""
    haystack = " ".join(parts)
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack, re.IGNORECASE) is not None


def default_ebay_source() -> Source:
    return Source(
        id="ebay",
        display_name="eBay",
        country="US",
        kind=SourceKind.MARKETPLACE,
        reliability=0.72,
        acquisition_method=AcquisitionMethod.API,
        base_url="https://www.ebay.com",
        notes="eBay Browse API (OAuth client credentials).",
    )


class EbayAdapter(SourceAdapter):
    """Search eBay listings via the official Browse API."""

    def __init__(
        self,
        source: Source | None = None,
        catalog: CatalogRepository | None = None,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        marketplace_id: str = "EBAY_US",
    ) -> None:
        self.source = source or default_ebay_source()
        self.catalog = catalog or CatalogRepository()
        self.settings = settings or get_settings()
        self.marketplace_id = marketplace_id
        self._client = client
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.settings.ebay_app_id and self.settings.ebay_cert_id)

    def unavailable_reason(self) -> str | None:
        if self.is_configured():
            return None
        return "eBay was not searched: EBAY_APP_ID / EBAY_CERT_ID are not set"

    async def fetch_listings(self, scope: SearchScope) -> list[dict[str, Any]]:
        """
        Raw Browse `itemSummaries` for this scope, before parsing or filtering.

        Separate from `search` so a caller can tell "eBay returned nothing" apart from
        "eBay returned listings that were all discarded" — two very different problems
        that produce the same empty offer list.
        """
        if not self.is_configured():
            logger.info("eBay adapter skipped — EBAY_APP_ID or EBAY_CERT_ID not set.")
            return []

        query = self.build_search_query(scope)
        if not query:
            return []

        try:
            token = await self._access_token()
            return await self._search_items(token, query)
        except SourceFetchError:
            raise
        except Exception as exc:
            logger.exception("eBay search failed for query=%r", query)
            raise SourceFetchError(f"eBay request failed: {exc}") from exc

    async def enrich(self, offers: list[Offer], scope: SearchScope) -> list[Offer]:
        """
        Read item specifics from `getItem` for listings the title could not place.

        `item_summary/search` returns a summary: no `localizedAspects`, no `gtin`, no
        `mpn`. Those live on the single-item resource, so a listing titled only
        "Samsung Galaxy S26 Ultra" carries nothing to match on until it is fetched —
        even though the seller filled in Storage Capacity and RAM as item specifics.

        One extra request per unplaced listing, capped, and each failure leaves its
        offer exactly as it was.
        """
        if not self.is_configured():
            return offers

        family = self.catalog.get_family(scope.family_id)
        valid_options = family.valid_options if family else {}

        resolved: list[Offer] = []
        budget = MAX_DETAIL_LOOKUPS
        for offer in offers:
            item_id = offer.retailer_sku
            if not item_id or budget <= 0:
                resolved.append(offer)
                continue
            budget -= 1
            try:
                detail = await self._get_item(item_id)
            except (SourceFetchError, httpx.HTTPError, ValueError) as exc:
                # Enrichment is an improvement, not a precondition: a listing that
                # cannot be detailed is no worse off than before the attempt.
                logger.warning("eBay item detail failed for %s: %s", item_id, exc)
                resolved.append(offer)
                continue
            resolved.append(self._with_detail(offer, detail, valid_options))
        return resolved

    async def _get_item(self, item_id: str) -> dict[str, Any]:
        token = await self._access_token()
        client = await self._get_client()
        response = await client.get(
            f"https://{self.api_host()}/buy/browse/v1/item/{item_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            timeout=15.0,
        )
        if response.is_error:
            raise SourceFetchError(
                f"HTTP {response.status_code}: {self._error_detail(response)}"
            )
        return dict(response.json())

    def _with_detail(
        self,
        offer: Offer,
        detail: dict[str, Any],
        valid_options: dict[str, list[Any]],
    ) -> Offer:
        """
        Fold item specifics into an offer, letting declared values beat parsed ones.

        A spec read from the item-specifics table was typed by the seller into a named
        field; the same spec read from a title was inferred from prose. Where both
        exist the declared one wins.
        """
        declared = {
            spec.key: spec
            for spec in (
                *self._specs_from_aspects(detail, valid_options),
                *self._specs_from_aspect_groups(detail, valid_options),
            )
        }
        gtin, mpn = _detail_identifiers(detail)
        new_stock = self._stock_status(detail)

        if not declared and not gtin and not mpn and new_stock == offer.stock_status:
            return offer

        merged = [spec for spec in offer.raw_specs if spec.key not in declared]
        merged.extend(declared.values())

        update: dict[str, Any] = {}
        if declared:
            update["raw_specs"] = merged
        if gtin and not offer.gtin:
            update["gtin"] = gtin
        if mpn and not offer.model_number:
            update["model_number"] = mpn
        if new_stock != offer.stock_status:
            update["stock_status"] = new_stock
            update["data_confidence"] = compute_data_confidence_from(
                self.source, offer.seller, new_stock
            )
        return offer.model_copy(update=update) if update else offer

    @staticmethod
    def _specs_from_aspect_groups(
        item: dict[str, Any],
        valid_options: dict[str, list[Any]],
    ) -> list[NormalizedSpec]:
        """
        Read `product.aspectGroups`, eBay's catalog-side specs.

        Sellers who list against an eBay catalogue product get these instead of, or as
        well as, their own item specifics, so both shapes have to be understood.
        """
        groups = (item.get("product") or {}).get("aspectGroups") or []
        flattened: list[dict[str, Any]] = []
        for group in groups:
            for aspect in group.get("aspects") or []:
                name = aspect.get("localizedName")
                values = aspect.get("localizedValues") or []
                if name and values:
                    flattened.append({"name": name, "value": values})
        return EbayAdapter._specs_from_aspects({"localizedAspects": flattened}, valid_options)

    async def search(self, scope: SearchScope, destination_country: str) -> list[Offer]:
        summaries = await self.fetch_listings(scope)
        if not summaries:
            return []

        # The Browse API returns a title and little else, so the family's option
        # lists are what let us read specs out of that title. Without them the
        # offer reaches the matcher with nothing to compare and is dropped.
        family = self.catalog.get_family(scope.family_id)
        valid_options = family.valid_options if family else {}

        offers: list[Offer] = []
        for item in summaries:
            offer = self._parse_item(item, valid_options)
            if offer is None or offer.stock_status == StockStatus.OUT_OF_STOCK:
                continue
            offers.append(offer)
        return offers

    def build_search_query(self, scope: SearchScope) -> str:
        """The keyword string sent to Browse. Public so it can be inspected directly."""
        family = self.catalog.get_family(scope.family_id)
        if family is None:
            return ""

        parts = [str(family.brand), str(family.family_name)]
        # Whatever the category made an identity key lands in constraints, so a laptop
        # search carries its RAM and chip and a tablet search carries its radio.
        for key, template in _QUERY_TERMS:
            value = scope.constraints.get(key)
            if value is None:
                continue
            term = template.format(value)
            # Family names often already carry the chip ("MacBook Air M4"), and
            # repeating it as a keyword ("... M4 M4 512GB") narrows a Browse search
            # against a phrase no seller writes.
            if not _already_in(term, parts):
                parts.append(term)
        return " ".join(parts)

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        host = self.api_host()
        credentials = f"{self.settings.ebay_app_id}:{self.settings.ebay_cert_id}"
        encoded = base64.b64encode(credentials.encode()).decode()
        client = await self._get_client()
        response = await client.post(
            f"https://{host}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
            timeout=15.0,
        )
        if response.is_error:
            # eBay answers bad credentials with `invalid_client`, which is the whole
            # answer to "are my keys working?" — so it is passed through verbatim.
            raise SourceFetchError(
                f"eBay OAuth rejected the credentials (HTTP {response.status_code}): "
                f"{self._error_detail(response)}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 7200))
        return self._token

    async def _search_items(self, token: str, query: str) -> list[dict[str, Any]]:
        host = self.api_host()
        client = await self._get_client()
        response = await client.get(
            f"https://{host}/buy/browse/v1/item_summary/search",
            params={"q": query, "limit": "20"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            timeout=15.0,
        )
        if response.is_error:
            raise SourceFetchError(
                f"eBay Browse search failed (HTTP {response.status_code}): "
                f"{self._error_detail(response)}"
            )
        return list(response.json().get("itemSummaries", []))

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Pull eBay's own error text out of a failed response, without the secrets."""
        try:
            payload = response.json()
        except ValueError:
            return response.text[:200].strip() or "no response body"
        for key in ("error_description", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = first.get("longMessage") or first.get("message")
                if isinstance(message, str) and message:
                    return message
        return str(payload)[:200]

    def api_host(self) -> str:
        """Which eBay host this adapter talks to. Public so it can be reported."""
        return "api.sandbox.ebay.com" if self.settings.ebay_sandbox else "api.ebay.com"

    def _parse_item(
        self,
        item: dict[str, Any],
        valid_options: dict[str, list[Any]],
    ) -> Offer | None:
        title = item.get("title")
        price_block = item.get("price") or {}
        amount = price_block.get("value")
        currency = price_block.get("currency")
        url = item.get("itemWebUrl")
        item_id = item.get("itemId")
        if not title or amount is None or not currency or not url or not item_id:
            return None

        seller_block = item.get("seller") or {}
        feedback = seller_block.get("feedbackPercentage")
        seller_score = float(feedback) / 100 if feedback is not None else None
        feedback_score = seller_block.get("feedbackScore")
        review_count = int(feedback_score) if feedback_score is not None else None
        seller = Seller(
            name=str(seller_block.get("username") or "eBay seller"),
            reliability=seller_score,
            review_count=review_count,
            is_official=False,
        )

        image = (item.get("image") or {}).get("imageUrl")
        condition = item.get("condition")
        gtin = None
        for aspect in item.get("localizedAspects") or []:
            name = str(aspect.get("name", "")).casefold()
            if name in {"gtin", "ean", "upc"}:
                values = aspect.get("value") or []
                if values:
                    gtin = str(values[0])
                    break

        stock_status = self._stock_status(item)
        confidence = compute_data_confidence_from(self.source, seller, stock_status)

        return Offer(
            id=f"ebay-{item_id}",
            source_id=self.source.id,
            seller=seller,
            country=self._item_country(item),
            listing_title=str(title),
            listing_url=str(url),
            image_url=image,
            list_price=Money(amount=Decimal(str(amount)), currency=str(currency)),
            retailer_sku=str(item_id),
            gtin=gtin,
            stock_status=stock_status,
            warranty=None,
            return_policy=None,
            raw_specs=[
                NormalizedSpec(key="title", value=title, raw_text=str(title)),
                *(
                    [NormalizedSpec(key="condition", value=condition, raw_text=str(condition))]
                    if condition
                    else []
                ),
                *self._specs_from_title(str(title), valid_options),
                *self._specs_from_aspects(item, valid_options),
            ],
            collected_at=datetime.now(timezone.utc),
            data_confidence=confidence,
        )

    @staticmethod
    def _specs_from_title(
        title: str,
        valid_options: dict[str, list[Any]],
    ) -> list[NormalizedSpec]:
        """
        Read catalog attributes out of the listing title.

        eBay publishes no structured specs on search results, so without this the
        offer carries only a title and a condition — nothing the attribute matcher
        can compare — and every listing ends up `unmatched` and dropped. The title
        is the one place a seller reliably states the build ("... 512GB 12GB RAM
        Unlocked EU"), so it is parsed with the same code that reads user queries.
        """
        attributes = parse_listing_attributes(title, valid_options)
        return [
            NormalizedSpec(key=key, value=value, raw_text=title)
            for key, value in attributes.items()
            if value is not None
        ]

    @staticmethod
    def _specs_from_aspects(
        item: dict[str, Any],
        valid_options: dict[str, list[Any]] | None = None,
    ) -> list[NormalizedSpec]:
        """
        Take specs from `localizedAspects` when a seller filled them in.

        These are more trustworthy than the title but optional and inconsistently
        named, so they supplement the title rather than replace it. Values arrive as
        display strings ("5,000 mAh", "6.9 in", "Schwarz") and go through the unit
        parser; colours are further mapped to English catalog labels.
        """
        from gp_price_intel.normalize.colour_aliases import canonicalize_colour

        options = valid_options or {}
        specs: list[NormalizedSpec] = []
        for aspect in item.get("localizedAspects") or []:
            key = ASPECT_SPEC_KEYS.get(str(aspect.get("name", "")).casefold())
            if key is None:
                continue
            values = aspect.get("value") or []
            raw = str(values[0]) if isinstance(values, list) and values else str(values or "")
            parsed = parse_spec_value(key, raw)
            if key == "colour" and parsed is not None:
                colours = [str(c) for c in options.get("colour", [])]
                english = canonicalize_colour(str(parsed), colours or None)
                if english is not None:
                    parsed = english
            if parsed is not None:
                specs.append(NormalizedSpec(key=key, value=parsed, raw_text=raw))
        return specs

    def _item_country(self, item: dict[str, Any]) -> str:
        location = item.get("itemLocation") or {}
        if location.get("country"):
            return str(location["country"])
        return MARKETPLACE_COUNTRY.get(self.marketplace_id, self.source.country)

    def _stock_status(self, item: dict[str, Any]) -> StockStatus:
        """
        Read stock from the listing. Browse search summaries usually omit it.

        `item_summary/search` returns currently listed items and does not include
        `estimatedAvailabilities` on the summary schema. Treating that absence as
        `unknown` then discounts every eBay offer by 15%, which — stacked on the
        cross-border completeness penalty — pushes even a 99%-rated seller below
        the highlight floor. A live search hit is in-stock until the payload says
        otherwise; `unknown` is reserved for an explicit unreadable status.
        """
        for availability in item.get("estimatedAvailabilities") or []:
            parsed = self._parse_availability_status(
                availability.get("estimatedAvailabilityStatus")
            )
            if parsed is not None:
                return parsed
        top_level = self._parse_availability_status(item.get("estimatedAvailabilityStatus"))
        if top_level is not None:
            return top_level
        return StockStatus.IN_STOCK

    @staticmethod
    def _parse_availability_status(raw: object) -> StockStatus | None:
        status = str(raw or "").casefold()
        if status in {"in_stock", "available"}:
            return StockStatus.IN_STOCK
        if status in {"limited", "low_stock"}:
            return StockStatus.LIMITED
        if status in {"out_of_stock", "sold_out", "unavailable"}:
            return StockStatus.OUT_OF_STOCK
        return None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
