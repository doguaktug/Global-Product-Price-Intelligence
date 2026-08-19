# Proposed decision algorithm

How the system turns raw offers into a recommendation — and explains it. This is the Week 1 "proposed algorithm" deliverable. Implementation will be Python.

---

## Why not just "cheapest wins"

The assignment asks: *which option is the best purchasing decision, and why?* Price is one factor. A user may care about warranty, seller trust, delivery speed, or specs. A cheaper list price in another country may have higher landed cost. Missing information should lower confidence, not be ignored.

The algorithm must:

1. Normalize heterogeneous criteria to a common scale
2. Apply **user-chosen weights** (or published defaults)
3. Handle **missing and unreliable** data honestly
4. Produce a **score** and a **plain-language explanation**
5. Select highlights (best price, best for you, …) and **guarded alternatives**

---

## Inputs

| Input | Source |
| --- | --- |
| `offers[]` | Matched offers with `matchKind = identical` for the confirmed variant |
| `nearOffers[]` | Offers with `matchKind = similar` or `different` (alternative candidates) |
| `preferences` | `UserPreferences`: weights, destination, reference currency |

Each offer already has: `listPrice`, `convertedListPrice`, `landedCost`, `seller`, `stockStatus`, `deliveryTime`, `warranty`, `returnPolicy`, `rawSpecs`, `dataConfidence`, `matchKind`.

---

## Step 1 — Criterion extraction

For each offer, extract raw criterion values from the data model fields:

| Criterion | Raw value | Source field(s) |
| --- | --- | --- |
| **price** | Landed cost total (reference currency) | `landedCost.total.amount`; fall back to `convertedListPrice.reference.amount` if landed cost is unavailable |
| **seller** | Seller reliability score | `seller.reliability` (0–1) × `source.reliability` (0–1) |
| **warranty** | Warranty strength | Parsed warranty duration in months; official/manufacturer warranty > third-party |
| **specs** | Spec match quality | How closely this offer's specs match or exceed the confirmed variant's canonical specs |
| **reviews** | User review score | Normalized rating (e.g. 4.5/5 → 0.9); penalize low review count |
| **delivery** | Delivery speed | Estimated days to destination; in-stock bonus |

Not every offer will have every field. That is handled in step 3.

---

## Step 2 — Normalization (0–1 scale)

Each criterion must be on the same 0–1 scale before weighting. Direction matters: lower price is better; higher rating is better.

### Min–max normalization within the offer set

For **lower-is-better** criteria (price, delivery days):

```
score_i = (max_value - value_i) / (max_value - min_value)
```

For **higher-is-better** criteria (seller, warranty, specs, reviews):

```
score_i = (value_i - min_value) / (max_value - min_value)
```

Edge case: if all values are identical (max = min), every offer scores **1.0** on that criterion — it is not a differentiator.

### Why min–max (not z-score or percentile)

- Bounded 0–1 output: easy to explain ("0.85 out of 1")
- Transparent: best in the set = 1, worst = 0
- Works with small offer sets (z-score needs larger N to be meaningful)
- Assignment scope: ≤ 20–30 offers per search, not millions

---

## Step 3 — Missing data handling

Real-world offers will have gaps. The algorithm does not guess; it penalizes and discloses.

| Situation | Rule |
| --- | --- |
| Criterion value is **missing** for this offer | That criterion is **excluded** from this offer's score; remaining weights are **re-normalized** to sum to 1 |
| Criterion value is missing for **all** offers | Criterion is dropped from the entire ranking round; weights re-normalized |
| `landedCost.completeness` is `partial` or `unknown` | Apply a **confidence penalty** (see below) |
| `dataConfidence` is low | Apply a confidence penalty |
| Conflicting specs between sources for the same offer | Use the source with higher reliability; log the conflict |

### Weight re-normalization (per offer)

If an offer is missing warranty and the user weights are `{ price: 0.50, seller: 0.25, warranty: 0.15, specs: 0.10 }`:

- Drop warranty → remaining `{ price: 0.50, seller: 0.25, specs: 0.10 }` sum = 0.85
- Re-normalize: `{ price: 0.588, seller: 0.294, specs: 0.118 }`

The offer is **not** kicked out — but it competes with less information, and `missingCriteria` records what was absent (for the explanation).

### Confidence penalty

```
effectiveScore = finalScore × confidenceMultiplier
```

Where:

```python
confidenceMultiplier = dataConfidence × completenessMultiplier

completenessMultiplier = {
    "complete": 1.0,
    "partial":  0.90,
    "unknown":  0.75,
}
```

A landed cost marked `partial` slightly down-ranks the offer; `unknown` is a stronger penalty. The explanation says why: "Total cost is estimated — shipping/duties not fully confirmed."

---

## Step 4 — Weighted scoring

```python
finalScore = confidenceMultiplier × Σ (w_c × score_c)   for each criterion c present
```

Where `w_c` are the **re-normalized** weights for this offer (after dropping missing criteria), and `score_c` is the 0–1 normalized value.

Example with all criteria present and default weights:

```
FinalScore = confidence × (
    0.50 × PriceScore
  + 0.25 × SellerScore
  + 0.15 × ReviewScore
  + 0.10 × DeliveryScore
)
```

---

## Step 5 — Highlight selection

After scoring, pick highlights. These are **not** just sorted-by-score; each lens has its own rule:

| Highlight | Selection rule |
| --- | --- |
| **Lowest list price** | Min `convertedListPrice.reference.amount` (sticker, ignoring fees) |
| **Lowest total cost** | Min `landedCost.total.amount` among offers with `completeness = complete` (prefer full estimates) |
| **Best seller / trust** | Max `sellerScore` (composite of seller + source reliability) |
| **Best warranty** | Max `warrantyScore` |
| **Best specification** | Max `specScore` — strongest specs vs confirmed variant |
| **Best for you** | Max `finalScore` — the weighted composite |

One offer can hold **multiple** highlight labels (e.g. best price AND best for you).

If "lowest total cost" and "lowest list price" are the **same** offer, show it once with both labels — do not duplicate.

---

## Step 6 — Alternative selection (guarded)

Candidates: offers in `nearOffers[]` with `matchKind = similar` or `matchKind = different`.

### 6a. Same-family spec variants

Offers where family is the same but specs differ (e.g. 512 GB vs 1 TB).

**Value test for upgrades:**

```python
specGainRatio  = specImprovement / baseSpec       # e.g. (1024 - 512) / 512 = 1.0 (100% more storage)
costGainRatio  = costIncrease / baseLandedCost    # e.g. extra 500 TL / 30000 TL = 0.017 (1.7%)

isGoodUpgrade  = specGainRatio >= UPGRADE_MIN_SPEC_GAIN     # e.g. 0.25 (25% spec jump)
              and costGainRatio <= UPGRADE_MAX_COST_INCREASE  # e.g. 0.10 (≤10% cost increase)
```

**Value test for downgrades:**

```python
costSavingRatio  = costSaving / baseLandedCost
specLossRatio    = specLoss / baseSpec

isGoodDowngrade  = costSavingRatio >= DOWNGRADE_MIN_COST_SAVING   # e.g. 0.15 (≥15% cheaper)
               and specLossRatio  <= DOWNGRADE_MAX_SPEC_LOSS      # e.g. 0.50 (lose ≤50% of the spec)
               and meetsMinimumRequirements                        # from confirmation gate
```

Thresholds are configurable; these defaults are illustrative.

### 6b. Comparable different products

Offers where `matchKind = different` but same category + comparable form factor.

```python
isComparable = sameCategory
           and attributeOverlapRatio >= 0.6     # ≥60% of core spec keys overlap
           and finalScore > bestOverall.finalScore × 0.85  # competitive with the best pick
```

### 6c. Selection and cap

- Collect all candidates that pass 6a or 6b
- Prefer **diversity of reason**: one upgrade, one downgrade, one rival — over three similar suggestions
- **Cap at ~3** alternatives
- Each gets its own explanation (what differs, cost delta, why it could beat the primary pick)
- If nothing passes the guardrails, show **zero** alternatives

---

## Step 7 — Explanation generation

Every highlight and alternative gets a plain-language **Explanation** before the Decision Page renders.

### Structure

```python
Explanation(
    headline="One-sentence verdict",
    reasons=[
        { "factor": "landed_cost", "detail": "Lowest total at 28,450 TL (list was not cheapest — shipping from DE is low)" },
        { "factor": "seller",      "detail": "Authorized retailer, reliability 0.92" },
    ],
    caveats=[
        "Import duty is estimated (±5%); final customs may differ",
        "Review count is low (12 reviews) — score may shift",
    ]
)
```

### Generation rules

1. **Headline:** state the highlight label + the single strongest reason.
2. **Reasons:** for each criterion where this offer scored ≥ 0.7 **or** was the decisive differentiator vs the runner-up, add a reason with the raw value and context.
3. **Caveats:** for each entry in `missingCriteria`, each `partial`/`unknown` landed-cost line, or any low `dataConfidence`, add a caveat.
4. **Comparison:** if the offer is the best-for-you but NOT the cheapest, explicitly say why the cheaper option lost (e.g. "Offer X is 2,100 TL cheaper but has no warranty and an unknown seller").
5. **Alternatives:** state what differs (spec change or product change), the landed-cost delta, and the value-test result.

The explanation must not be a score dump. It must read like a short purchasing argument.

---

## Configurable parameters (summary)

| Parameter | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_WEIGHTS` | `{ price: 0.50, seller: 0.25, reviews: 0.15, delivery: 0.10 }` | Applied when user does not move sliders |
| `COMPLETENESS_MULTIPLIER` | `{ complete: 1.0, partial: 0.90, unknown: 0.75 }` | Landed-cost confidence penalty |
| `UPGRADE_MIN_SPEC_GAIN` | `0.25` | Min relative spec improvement for an upgrade alternative |
| `UPGRADE_MAX_COST_INCREASE` | `0.10` | Max relative cost increase for an upgrade alternative |
| `DOWNGRADE_MIN_COST_SAVING` | `0.15` | Min relative cost saving for a downgrade alternative |
| `DOWNGRADE_MAX_SPEC_LOSS` | `0.50` | Max relative spec loss for a downgrade alternative |
| `COMPARABLE_OVERLAP_RATIO` | `0.60` | Min attribute overlap for a different-product alternative |
| `COMPARABLE_SCORE_FLOOR` | `0.85` | Min finalScore ratio vs best-overall for a rival alternative |
| `MAX_ALTERNATIVES` | `3` | Cap on alternative suggestions |

---

## Why this methodology (for the assignment)

| Assignment question | Answer |
| --- | --- |
| Why this methodology? | Weighted multi-criteria scoring: transparent, explainable, user-controllable, handles missing data |
| Which variables? | Landed cost, seller trust, warranty, specs, reviews, delivery — extensible |
| How normalized? | Min–max within the offer set per criterion; bounded 0–1 |
| How weighted? | User-chosen sliders (or published defaults); weights re-normalize per offer for missing criteria |
| How is the final ranking calculated? | `finalScore = confidenceMultiplier × Σ(w × score)` then pick highlights by lens |
| Missing information? | Criterion excluded for that offer; weights re-normalized; `missingCriteria` in explanation |
| Unreliable information? | `confidenceMultiplier` from `dataConfidence` × completeness; explanation caveat |
| How does it explain? | Headline + reasons (decisive factors with values) + caveats (gaps/estimates) |

---

## Pseudocode overview

```python
def decide(offers, near_offers, preferences):
    # 1. Extract raw criterion values
    for offer in offers:
        offer.criteria = extract_criteria(offer)

    # 2. Normalize to 0–1
    normalize_criteria(offers)

    # 3. Score each offer
    for offer in offers:
        available = {c: v for c, v in offer.criteria.items() if v is not None}
        weights = renormalize_weights(preferences.weights, available.keys())
        raw_score = sum(weights[c] * available[c] for c in available)
        offer.confidence = compute_confidence(offer)
        offer.final_score = raw_score * offer.confidence
        offer.missing = [c for c in preferences.weights if c not in available]

    # 4. Pick highlights
    highlights = {
        "lowest_list_price":  min(offers, key=lambda o: o.converted_list_price),
        "lowest_total_cost":  min(complete_offers, key=lambda o: o.landed_cost_total),
        "best_seller":        max(offers, key=lambda o: o.criteria.get("seller", 0)),
        "best_warranty":      max(offers, key=lambda o: o.criteria.get("warranty", 0)),
        "best_specification": max(offers, key=lambda o: o.criteria.get("specs", 0)),
        "best_for_you":       max(offers, key=lambda o: o.final_score),
    }

    # 5. Scout alternatives (guarded)
    alternatives = select_alternatives(near_offers, highlights["best_for_you"], preferences)

    # 6. Build explanations
    for label, offer in highlights.items():
        offer.explanation = build_explanation(offer, label, offers, preferences)
    for alt in alternatives:
        alt.explanation = build_alt_explanation(alt, highlights["best_for_you"], preferences)

    return DecisionPage(highlights=highlights, alternatives=alternatives)
```

Implementation in Python (dataclasses, no heavy ML needed for v1). The ranking engine, explanation builder, and alternative scout are separate modules matching the architecture's service boundaries.
