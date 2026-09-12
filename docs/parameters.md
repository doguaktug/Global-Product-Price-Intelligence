# Parameter reference

Every tunable number in the system, where it is defined, and what moving it does.

This exists because the other documents quote thresholds inline ("≥25% spec gain", "confidence floor 0.7") and those quotes drift. The values below are read from the modules named in the **Defined in** column — that module is the source of truth, and this page is the index into it.

Not listed here: regular expressions (delivery/warranty/unit parsing), token stoplists, and the fixture data files. Those are not tuning knobs; changing them changes behaviour qualitatively rather than by degree.

---

## Decision weights

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `DEFAULT_WEIGHTS` | `price 0.40, seller 0.20, reviews 0.15, delivery 0.10, warranty 0.15` | `domain/models.py` | Applied when the user does not move the sliders |

Weights are **relative**. The engine re-normalizes whatever it is given, per offer, over the criteria that offer actually has — so only the proportions matter, they need not sum to 1, and pushing every slider to maximum gives the same ranking as leaving them all low. A weight of 0 removes the criterion entirely.

There is deliberately no spec weight: every offer in the ranked list matched the confirmed build, so a spec score would be identical across the set. See [proposed-algorithm.md](proposed-algorithm.md) step 1.

---

## Scoring and confidence

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `SOURCE_RELIABILITY_WEIGHT` | `0.30` | `ranking/engine.py` | How much the hosting site's reputation moves the seller criterion. The seller's own record carries the remaining 0.70 |
| `_COMPLETENESS_MULTIPLIER` | `complete 1.0, partial 0.90, unknown 0.75` | `ranking/engine.py` | Penalty applied for how much of the landed cost had to be estimated |
| `UNKNOWN_STOCK_CONFIDENCE_FACTOR` | `0.85` | `ranking/confidence.py` | Applied only when stock status is `unknown`. In-stock and out-of-stock are both *known* and are not penalised |
| `HIGHLIGHT_MIN_CONFIDENCE` | `0.7` | `ranking/confidence.py` | Effective-confidence floor to be eligible for a highlight card. Below it an offer still appears in the ranked list, carrying a warning |

`SOURCE_RELIABILITY_WEIGHT` is a blend rather than a multiplication on purpose: multiplying would let a mid-tier marketplace cap an excellent seller at `0.95 × 0.5 = 0.475`, indistinguishable from a mediocre one.

Raising `HIGHLIGHT_MIN_CONFIDENCE` makes the Decision Page quieter and more conservative — more lenses come back empty. Lowering it recommends offers the system is less sure about.

---

## Alternatives

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `UPGRADE_MIN_SPEC_GAIN` | `0.25` | `alternatives/scout.py` | Minimum relative gain on some spec to earn the `upgrade` badge |
| `UPGRADE_MAX_COST_INCREASE` | `0.10` | `alternatives/scout.py` | Maximum relative cost increase still counted as a worthwhile upgrade |
| `DOWNGRADE_MIN_COST_SAVING` | `0.15` | `alternatives/scout.py` | Minimum relative saving to earn the `downgrade` badge |
| `DOWNGRADE_MAX_SPEC_LOSS` | `0.50` | `alternatives/scout.py` | No spec may fall further than this for a downgrade to be recommended |
| `COMPARABLE_OVERLAP_RATIO` | `0.60` | `alternatives/scout.py` | Share of comparable specs a different product must agree on to earn the `rival` badge |
| `COMPARABLE_SCORE_FLOOR` | `0.85` | `alternatives/scout.py` | A rival's score must exceed this fraction of the top pick's |
| `MAX_ALTERNATIVES` | `3` | `alternatives/scout.py` | Cap on the alternatives panel |

These gate the **badge, not the listing**. A near-offer that clears none is still shown and still ranked; it simply carries no claim. Loosening them therefore does not surface more options, it attaches more claims to the options already there.

`COMPARABLE_SCORE_FLOOR` only means anything because alternatives are scored in the same normalization pass as the ranked list. If that changes, this parameter stops being comparable to anything.

---

## Explanations

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `STRONG_CRITERION_SCORE` | `0.7` | `explanation/builder.py` | A criterion scoring at least this well is worth stating even when it was not decisive |
| `MAX_DECISIVE_REASONS` | `3` | `explanation/builder.py` | Cap on criterion reasons in one explanation. More than this reads as a score dump |

---

## Query normalization and confirmation

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `FAMILY_MATCH_THRESHOLD` | `0.45` | `normalize/query_normalizer.py` | Similarity a family must reach to be accepted without asking the user |
| `FAMILY_AMBIGUITY_GAP` | `0.06` | `normalize/query_normalizer.py` | If the runner-up family is within this of the leader, the match is ambiguous and the popup asks |
| `FAMILY_SUGGESTION_THRESHOLD` | `0.30` | `normalize/query_normalizer.py` | Floor to be offered as a "did you mean" option at all |
| `FAMILY_OPTION_LIMIT` | `5` | `normalize/query_normalizer.py` | Most families the popup will offer |
| `CLOSEST_VARIANT_LIMIT` | `3` | `normalize/query_normalizer.py` | Most "closest build" suggestions when the family is right but the exact build is not stocked |
| `COMPACT_QUERY_MAX_LEN` | `8` | `normalize/similarity.py` | Length below which a query is treated as a compact alias (`"s26u"`) rather than prose |
| `COMPACT_ALIAS_MATCH_THRESHOLD` | `80` | `normalize/similarity.py` | Fuzzy score (rapidfuzz 0–100) a compact alias must reach |

Lowering `FAMILY_MATCH_THRESHOLD` accepts more queries silently and risks searching the wrong product; raising it asks more often. `FAMILY_AMBIGUITY_GAP` trades the same way in the other direction — widening it prefers asking over guessing.

---

## Unit normalization

| Parameter | Default | Defined in | Effect |
| --- | --- | --- | --- |
| `_GB_PER_TB` | `1024` | `normalize/spec_parser.py` | What a listing's "1 TB" becomes in `storage_gb`. Must agree with the catalog, which stores 1 TB builds as `1024` |
| `_MM_PER_INCH` | `25.4` | `normalize/spec_parser.py` | Metric→imperial conversion for display sizes |
| `_DISPLAY_PRECISION` | `1` | `normalize/spec_parser.py` | Decimal places kept when converting a display size |

`_DISPLAY_PRECISION` is a correctness parameter, not a cosmetic one. A listing's `"17,5 cm"` converts to 6.889…, and comparing that against a catalog `6.9` as an exact value would make the offer fail to match its own product. Rounding to the precision the source would have printed is what keeps the comparison honest.

---

## Landed cost

Rate tables rather than single values, all in `landed_cost/service.py`.

| Parameter | Default | Effect |
| --- | --- | --- |
| `FEE_CURRENCY` | `USD` | Currency flat estimates are authored in, before conversion to the user's reference currency |
| `_CATEGORY_SHIPPING_FACTOR` | `smartphone 1.0, tablet 1.4, laptop 2.2` | Parcel-size multiplier on the lane rate |
| `_VAT_RATE` | `TR 0.20, DE 0.19, GB 0.20, US 0.00, JP 0.10` | Destination VAT, and the origin VAT removed on export |
| `_DEFAULT_VAT_RATE` | `0.10` | Used for a destination not in the table — marks the estimate as guessed |
| `_DUTY_RATE` | `TR 0.10, DE 0.00, GB 0.00, US 0.03, JP 0.00` | Customs duty by destination |
| `_DUTY_RATE_BY_CATEGORY` | `(TR, smartphone) 0.20, (TR, tablet) 0.10, (TR, laptop) 0.00, (US, smartphone) 0.00` | Overrides the per-destination rate where category matters |
| `_DEFAULT_DUTY_RATE` | `0.05` | Fallback duty — also marks the estimate as guessed |
| `_REGISTRATION_FEE` | `(TR, smartphone) 1150` | Import registration levies. Türkiye registers handset IMEIs; tablets and laptops are not registered |
| `CENTS` | `0.01` | Rounding quantum for money |

Shipping itself is not a constant: it is looked up per origin→destination lane in `data/fixtures/shipping_lanes.json`. When no lane is published, a generic figure stands in and the whole estimate is downgraded to `unknown` completeness — a guess about the largest add-on should not be presented as a total.

The distinction the `_DEFAULT_*` entries carry is the point of `LandedCostCompleteness`: a rate that was **looked up** yields `complete`, and a rate that was **invented** yields `unknown`, so the Decision Page can tell the user which it is.

---

## Environment settings

Read from the environment or `.env` via `config.py` (`Settings`), not from code constants.

| Setting | Default | Effect |
| --- | --- | --- |
| `DEFAULT_DESTINATION_COUNTRY` | `TR` | Where the user is buying to, before geolocation or a manual override |
| `DEFAULT_REFERENCE_CURRENCY` | `TRY` | Currency every offer is compared in |
| `EBAY_APP_ID` / `EBAY_CERT_ID` | unset | eBay Browse API credentials. Without them the eBay adapter returns nothing and other sources continue |
| `EBAY_SANDBOX` | `false` | Point the eBay adapter at the sandbox host |
| `DATA_DIR` | `<repo>/data` | Where catalog, sources and fixtures are loaded from |

Three settings are declared but **read by nothing today**, and are listed separately rather than described as if they worked:

| Setting | Default | Intent |
| --- | --- | --- |
| `FX_PROVIDER` | `frankfurter` | Selecting between FX providers. Frankfurter is currently wired in directly, so the value is ignored |
| `OFFER_CACHE_TTL_SECONDS` | `900` | Lifetime of a cached search — see [data-model.md](data-model.md#where-a-cache-would-go-if-one-is-added) |
| `EBAY_DEV_ID` | unset | Required by eBay's Trading-era APIs; the Browse API does not use it |
