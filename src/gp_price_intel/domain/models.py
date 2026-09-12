"""Domain entities and value objects (see docs/data-model.md)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Enums ---


class AcquisitionMethod(str, Enum):
    API = "api"
    HTML = "html"
    HEADLESS = "headless"
    FIXTURE = "fixture"


class SourceKind(str, Enum):
    MANUFACTURER = "manufacturer"
    AUTHORIZED_RETAILER = "authorized_retailer"
    MARKETPLACE = "marketplace"
    LOCAL_RETAILER = "local_retailer"
    OTHER = "other"


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    LIMITED = "limited"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class MatchKind(str, Enum):
    IDENTICAL = "identical"
    SIMILAR = "similar"
    DIFFERENT = "different"
    UNMATCHED = "unmatched"


class CostOrigin(str, Enum):
    QUOTED = "quoted"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class LandedCostCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PreferenceOrigin(str, Enum):
    DEFAULT = "default"
    GEOLOCATION = "geolocation"
    MANUAL = "manual"


class PropertyRole(str, Enum):
    IDENTITY = "identity"
    OPTIONAL = "optional"


class ConfirmationReason(str, Enum):
    MISSING = "missing"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    SHORTHAND = "shorthand"  # compact/abbreviated match — confirm family before proceeding
    NO_MATCH = "no_match"  # nothing in the catalog is close enough to search for
    NO_EXACT_VARIANT = "no_exact_variant"  # family is known, the asked-for build is not stocked


class PropertyChoiceKind(str, Enum):
    VALUE = "value"
    NOT_IMPORTANT = "not_important"


class SessionStatus(str, Enum):
    RECEIVED = "received"
    NEEDS_CONFIRMATION = "needs_confirmation"
    FETCHING = "fetching"
    RANKED = "ranked"
    FAILED = "failed"


class HighlightKind(str, Enum):
    LOWEST_LIST_PRICE = "lowest_list_price"
    LOWEST_TOTAL_COST = "lowest_total_cost"
    BEST_WARRANTY = "best_warranty"
    BEST_SELLER = "best_seller"
    BEST_OVERALL = "best_overall"


DEFAULT_WEIGHTS: dict[str, float] = {
    "price": 0.40,
    "seller": 0.20,
    "reviews": 0.15,
    "delivery": 0.10,
    "warranty": 0.15,
}


class AlternativeKind(str, Enum):
    SPEC_VARIANT = "spec_variant"
    COMPARABLE_PRODUCT = "comparable_product"


class AlternativeBadge(str, Enum):
    """
    Why an alternative is worth a second look, when it passes the value tests.

    Unbadged alternatives are still shown and still ranked — they simply did not
    clear a threshold that makes a specific case for them.
    """

    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    RIVAL = "rival"


# --- Value objects ---


class Money(BaseModel):
    model_config = {"frozen": True}

    amount: Decimal
    currency: str


class FxQuote(BaseModel):
    model_config = {"frozen": True}

    base_currency: str
    quote_currency: str
    rate: Decimal
    #: When the *provider* published this rate, not when we fetched it. ECB rates are
    #: published once per business day, so this is a date, and two searches minutes
    #: apart legitimately share it. None when no conversion happened (see `is_identity`).
    as_of: datetime | None = None
    provider: str

    @property
    def is_identity(self) -> bool:
        """True when base and quote are the same currency, so no rate was involved."""
        return self.base_currency.upper() == self.quote_currency.upper()


class ConvertedMoney(BaseModel):
    model_config = {"frozen": True}

    original: Money
    reference: Money
    fx: FxQuote


class NormalizedSpec(BaseModel):
    model_config = {"frozen": True}

    key: str
    value: Any
    unit: str | None = None
    raw_text: str | None = None


class CostLine(BaseModel):
    model_config = {"frozen": True}

    amount: Money
    origin: CostOrigin
    label: str


class LandedCost(BaseModel):
    model_config = {"frozen": True}

    list_in_reference: Money
    shipping: CostLine | None = None
    taxes: CostLine | None = None
    import_duties: CostLine | None = None
    registration_fees: CostLine | None = None
    other_fees: list[CostLine] = Field(default_factory=list)
    total: Money
    completeness: LandedCostCompleteness
    destination_country: str


# --- Catalog ---


class Category(BaseModel):
    id: str
    core_spec_keys: list[str] = Field(default_factory=list)
    identity_keys: list[str] = Field(default_factory=list)
    optional_keys: list[str] = Field(default_factory=list)


class ProductFamily(BaseModel):
    id: str
    category_id: str
    brand: str
    family_name: str
    aliases: list[str] = Field(default_factory=list)
    valid_options: dict[str, list[Any]] = Field(default_factory=dict)


class ProductVariant(BaseModel):
    id: str
    family_id: str
    model_name: str
    model_number: str | None = None
    gtin: str | None = None
    retailer_skus: dict[str, str] = Field(default_factory=dict)
    storage_gb: int | None = None
    memory_gb: int | None = None
    region_version: str | None = None
    colour: str | None = None
    processor: str | None = None
    connectivity: str | None = None
    canonical_specs: list[NormalizedSpec] = Field(default_factory=list)

    def attribute(self, key: str) -> Any:
        """
        Read a spec by key from the declared fields, then from ``canonical_specs``.

        Category spec keys (``display_inch``, ``battery_mah``) live only in
        ``canonical_specs``, so callers must not use ``getattr`` directly.
        """
        value = getattr(self, key, None)
        if value is not None:
            return value
        for spec in self.canonical_specs:
            if spec.key == key:
                return spec.value
        return None


# --- Sources & offers ---


class Source(BaseModel):
    id: str
    display_name: str
    country: str
    kind: SourceKind
    reliability: float = Field(ge=0, le=1)
    acquisition_method: AcquisitionMethod
    base_url: str | None = None
    notes: str | None = None


class Seller(BaseModel):
    name: str
    reliability: float | None = Field(default=None, ge=0, le=1)
    review_count: int | None = Field(default=None, ge=0)
    is_official: bool | None = None


class Offer(BaseModel):
    id: str
    source_id: str
    seller: Seller
    country: str
    listing_title: str
    listing_url: str
    image_url: str | None = None
    list_price: Money
    converted_list_price: ConvertedMoney | None = None
    landed_cost: LandedCost | None = None
    stock_status: StockStatus | None = StockStatus.UNKNOWN
    delivery_time: str | None = None
    warranty: str | None = None
    return_policy: str | None = None
    retailer_sku: str | None = None
    gtin: str | None = None
    model_number: str | None = None
    raw_specs: list[NormalizedSpec] = Field(default_factory=list)
    matched_variant_id: str | None = None
    match_kind: MatchKind = MatchKind.UNMATCHED
    match_notes: list[str] = Field(default_factory=list)
    collected_at: datetime
    data_confidence: float = Field(default=1.0, ge=0, le=1)


# --- Session / confirmation ---


class UserPreferences(BaseModel):
    destination_country: str = "TR"
    reference_currency: str = "TRY"
    origin: PreferenceOrigin = PreferenceOrigin.DEFAULT
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


class ConfirmationPrompt(BaseModel):
    property_key: str
    role: PropertyRole
    reason: ConfirmationReason
    options: list[Any]
    allow_not_important: bool = False


class PropertyChoice(BaseModel):
    property_key: str
    kind: PropertyChoiceKind
    value: Any | None = None


class NormalizedQuery(BaseModel):
    raw_text: str
    extracted: dict[str, Any] = Field(default_factory=dict)
    candidate_family_id: str | None = None
    candidate_variant_ids: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False
    pending_properties: list[ConfirmationPrompt] = Field(default_factory=list)


class SearchScope(BaseModel):
    family_id: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    unconstrained_keys: list[str] = Field(default_factory=list)
    variant_ids: list[str] = Field(default_factory=list)


class SearchSession(BaseModel):
    id: str
    raw_query: str
    normalized_query: NormalizedQuery | None = None
    property_choices: list[PropertyChoice] = Field(default_factory=list)
    search_scope: SearchScope | None = None
    confirmed_variant_id: str | None = None
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    status: SessionStatus = SessionStatus.RECEIVED
    failure_reason: str | None = None
    created_at: datetime


# --- Decision ---


class ExplanationReason(BaseModel):
    factor: str
    detail: str


class Explanation(BaseModel):
    headline: str
    reasons: list[ExplanationReason] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    weights_used: dict[str, float] = Field(default_factory=dict)
    missing_criteria: list[str] = Field(default_factory=list)
    confidence_penalty: float = 0.0
    final_score: float = 0.0
    reliability_warning: str | None = None
    explanation: Explanation | None = None


class DecisionHighlight(BaseModel):
    kind: HighlightKind
    offer_id: str
    explanation: Explanation


class Alternative(BaseModel):
    offer_id: str
    kind: AlternativeKind
    #: Set when the alternative clears one of the documented value tests.
    badge: AlternativeBadge | None = None
    differing_attributes: list[str] = Field(default_factory=list)
    #: Difference against the top pick's landed cost, not this offer's own total.
    #: Negative means this alternative is cheaper.
    landed_cost_delta: Money | None = None
    explanation: Explanation


class DecisionPage(BaseModel):
    session_id: str
    confirmed_variant: ProductVariant | None = None
    offers: list[Offer] = Field(default_factory=list)
    offer_scores: dict[str, ScoreBreakdown] = Field(default_factory=dict)
    highlights: list[DecisionHighlight] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    generated_at: datetime
