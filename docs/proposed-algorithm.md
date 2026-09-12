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
5. Select highlights (best price, best for you, …) and rank **alternatives**, badging the ones that pass a value test

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
| **price** | Landed cost total (reference currency) | `landedCost.total.amount`; falls back to `convertedListPrice.reference.amount` if landed cost is unavailable |
| **seller** | Seller trust | `0.70 × seller.reliability + 0.30 × source.reliability` (both 0–1) |
| **reviews** | Review weight | `reviewVolumeScore(seller.reviewCount)`, log-scaled; falls back to `seller.reliability`, then `dataConfidence`, when the source publishes no count |
| **delivery** | Delivery speed | `deliveryTime` parsed into days |
| **warranty** | Warranty length | `warranty` parsed into months |

Every criterion is read in the **reference currency or a parsed unit** — never a raw source string. Offers whose price cannot be converted are dropped upstream, so the price criterion never compares mixed currencies.

### Why the seller criterion blends two reputations

A listing's trustworthiness is partly the seller and partly the site carrying it. Multiplying the two would let a mid-tier marketplace cap an excellent seller (`0.95 × 0.5 = 0.475`, indistinguishable from a mediocre seller). Instead the site's reputation is a **30% influence** on the criterion, so it moves the ranking without overruling the seller's own record. Seller reliability is currently hand-set per source/fixture; a later iteration may derive it from published traffic and feedback statistics.

### There is no spec criterion

Only offers that matched the confirmed variant as `identical` are ranked, and identical offers share the same specs — a spec score would be 1.0 for every one of them and contribute nothing. Specs decide **which** offers enter the ranking set (matching) and **which alternatives are worth showing** (AlternativeScout), not the score inside the set. Accordingly, specs appear in neither `DEFAULT_WEIGHTS` nor the user's weight sliders.

Not every offer will have every field. That is handled in step 3.

---

## Step 2 — Normalization (0–1 scale)

Each criterion must be on the same 0–1 scale before weighting. Direction matters: lower price is better; higher rating is better.

### Min–max normalization within the offer set

For **lower-is-better** criteria (price, delivery days):

```
score_i = (max_value - value_i) / (max_value - min_value)
```

For **higher-is-better** criteria (seller, reviews, warranty):

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
| Criterion value is present but **unparseable** | Treated as missing — see "No invented numbers" below |
| Criterion value is missing for **all** offers | Criterion is dropped from the entire ranking round; weights re-normalized |
| `landedCost.completeness` is `partial` or `unknown` | Apply a **confidence penalty** (see below) |
| `dataConfidence` is low | Apply a confidence penalty |
| Conflicting specs between sources for the same offer | Use the source with higher reliability; log the conflict |

### No invented numbers

`deliveryTime` and `warranty` arrive as free text written by each source in its own language: `"2-4 Werktage"`, `"1-3 iş günü"`, `"2-5日"`, `"Next day"`, `"24 months"`. The parsers convert these to days and months respectively, using the **unit word** to pick the scale and reading a quoted range as its **slowest end** (a "2-4 day" promise is a 4-day wait for the buyer).

When the text carries no unit the parser returns nothing rather than guessing — `"48"` could be hours, days or a warranty in months, and a wrong guess silently moves the ranking. A bare `"manufacturer warranty"` likewise yields no duration. In both cases the criterion joins `missingCriteria` for that offer and its weight is redistributed, which is honest and visible in the explanation. The alternative — substituting a neutral default like 0.5 — would fabricate a comparison the data does not support.

### Weight re-normalization (per offer)

If an offer is missing warranty and the user weights are `{ price: 0.40, seller: 0.20, warranty: 0.15, reviews: 0.15, delivery: 0.10 }`:

- Drop warranty → remaining `{ price: 0.40, seller: 0.20, reviews: 0.15, delivery: 0.10 }` sum = 0.85
- Re-normalize: `{ price: 0.471, seller: 0.235, reviews: 0.176, delivery: 0.118 }`

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

### Weights are relative, not absolute

Only the **ratios** between weights matter. The engine re-normalizes whatever it is given to sum to 1 before scoring, so `{ price: 0.40, seller: 0.20 }` and `{ price: 4, seller: 2 }` rank identically, and a user who drags every slider to the top gets the same result as one who leaves them all at the bottom. This has two useful consequences:

- **The UI does not have to police the sliders.** Sliders can move independently; nothing needs to "steal" from a neighbour to keep a total of 100%.
- **Published defaults stay comparable to custom weights.** `DEFAULT_WEIGHTS` happens to sum to 1.0 for readability, but that is a presentation choice, not a requirement.

A criterion given a weight of **zero is removed** from the round entirely — it is not scored, not re-normalized against, and does not appear in `weightsUsed`. That is how a user says "I genuinely do not care about warranty" as opposed to "warranty barely matters".

Example with all criteria present and default weights:

```
FinalScore = confidence × (
    0.40 × PriceScore
  + 0.20 × SellerScore
  + 0.15 × ReviewScore
  + 0.10 × DeliveryScore
  + 0.15 × WarrantyScore
)
```

---

## Step 5 — Highlight selection

After scoring, pick highlights **only from offers that clear the confidence floor**.

```
HIGHLIGHT_MIN_CONFIDENCE = 0.7
effectiveConfidence = dataConfidence × completenessMultiplier
                     = 1 − confidencePenalty
```

If `effectiveConfidence < 0.7`, the offer:

- **stays** in the full ranked `offers` list (sorted by `finalScore`)
- gets a `reliabilityWarning` / explanation caveat
- is **excluded** from Decision Page recommendation lenses

Eligible offers are then selected by lens (not only by overall score):

| Highlight | Selection rule (eligible pool only) |
| --- | --- |
| **Best for you** | Max `finalScore` — the weighted composite. Selected first. |
| **Lowest list price** | Min `convertedListPrice.reference.amount` (sticker, ignoring fees) |
| **Lowest total cost** | Min `landedCost.total.amount` among offers with usable landed-cost completeness |
| **Best seller / trust** | Max `sellerScore` (composite of seller + source reliability) |
| **Best warranty** | Max parsed warranty duration among eligible offers that have warranty data |

If “best for you” is the same offer as another lens, **drop the other highlight**. Identical-product offers share the same specs, so there is no “best specification” Decision Page lens. AlternativeScout still compares specs when choosing close (same-family variant) or far (comparable product) alternatives.

One offer holds at most one highlight label.

---

## Step 6 — Alternative selection and badging

Candidates: offers in `nearOffers[]` with `matchKind = similar` or `matchKind = different`.

### 6.0. Alternatives are scored in the same pass as the ranked list

Near-offers go through steps 1–4 **together with** the identical-match offers, in one normalization pass, and are only split apart afterwards on `matchKind`. This is not an optimization — it is what makes the numbers mean anything. Min–max normalization (step 2) is relative to the set it is given, so an alternative scored in its own pass would get a score calibrated against other alternatives. Comparing that to the top pick's score would be meaningless, and the rival test below ("within 85% of the best score") would be comparing two different scales.

Alternatives are therefore **ranked by `finalScore`** exactly like the main list, and their scores are directly comparable to it.

### 6.0b. The value tests award badges; they do not filter

Every candidate that survives matching is shown, ranked. What the value tests decide is whether it carries a **badge** — `upgrade`, `downgrade`, or `rival` — which is a claim the system is making about the offer's value. An alternative that clears no test keeps its place in the list but loses the claim, and says so in its caveats ("Shown for comparison — it does not clear a value test").

The reason is that a badge and a listing answer different questions. "Is this worth your money?" deserves a guarded answer; "does this option exist?" does not. Suppressing an unbadged near-offer hides a real option from the user and makes the alternatives panel look empty for no stated reason.

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

**`specGain` and `specLoss` are measured per spec, not on one hand-picked field.** Every numeric key in the category's identity, optional, and core spec lists is compared between the confirmed variant and the candidate; the largest gain and the largest loss are what the thresholds are tested against. So "≥25% gain" means *some* spec improved by at least that much, and "≤50% loss" means *nothing* fell further than that. This matters because a laptop that doubles its RAM while keeping the same storage is a genuine upgrade, and a rule that only looked at storage would miss it.

### 6b. Comparable different products

Offers where `matchKind = different` but same category + comparable form factor.

```python
isComparable = sameCategory
           and attributeOverlapRatio >= 0.6     # ≥60% of core spec keys overlap
           and finalScore > bestOverall.finalScore × 0.85  # competitive with the best pick
```

Both halves are required, and they check different things: overlap asks whether the product answers the same need, and the score floor asks whether it is close enough to be worth switching to. A cheap product that shares most specs but scores poorly is not a contender, and a high scorer that shares few specs is not comparable.

### 6c. Cost delta

Each alternative carries `landedCostDelta`: **its landed cost minus the top pick's**, not its own total. Negative means the alternative is cheaper. It is a delta rather than an absolute because the alternatives panel exists to answer "what would switching cost me?", and a reader should not have to subtract two totals to find out. The absolute total is still on the offer itself for anyone who wants it.

### 6d. Selection and cap

- Rank all candidates by `finalScore`
- **Cap at 3** alternatives
- Prefer **diversity of reason**: fill slots with one upgrade, one downgrade, and one rival before topping up with the highest-scoring unbadged candidates. Three cheaper-but-smaller variants tell the user one thing three times
- Each gets its own explanation (what differs, cost delta, and either the value test it passed or a caveat that it passed none)
- If there are no near-offers at all, show zero alternatives — but a near-offer is never dropped for failing a value test

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
2. **Reasons must be the criteria that actually won the comparison.** For each criterion, compute this offer's **weighted contribution** (`weightUsed × criterionScore`) minus the runner-up's on the same criterion. Every criterion with a positive margin is a reason the offer is where it is; they are stated **largest margin first** and capped at `MAX_DECISIVE_REASONS` (3). Criteria the offer merely scored ≥ 0.7 on are appended after those, so a strong all-rounder still reads as one — but they never displace a decisive reason.
3. **Caveats:** for each entry in `missingCriteria`, each `partial`/`unknown` landed-cost line, or any low `dataConfidence`, add a caveat.
4. **Comparison:** if the offer is the best-for-you but NOT the cheapest, name the cheapest offer it beat and the criteria that offer lost on (e.g. "Bargain Bin's listing is 2,100 TL cheaper but loses on seller trust, no stated warranty").
5. **Alternatives:** state what differs (spec change or product change), the landed-cost delta against the top pick, and either the value test passed or a caveat that none was.

### Why margins, and why against the runner-up

The explanation has to answer "why did *this* offer win?", so the reasons have to be the things that made it win. Two rules follow from that.

**Weighted, not raw.** A criterion the user weighted at 5% cannot be the reason for anything, however well the offer scored on it. Comparing weighted contributions means a reason is only offered when it carried real influence under *this* user's weights.

**Against the runner-up, not the field.** The relevant comparison is the offer this one actually had to beat: second place for the winner, and the offer directly above it for anyone further down. Averaging over the whole field would let a criterion where the offer beats a few weak listings look decisive when it changed nothing at the top.

The consequence worth noting: a criterion the offer scores well on but *ties or loses* on is not presented as a reason it won, because it was not one.

The explanation must not be a score dump. It must read like a short purchasing argument.

---

## Configurable parameters (summary)

| Parameter | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_WEIGHTS` | `{ price: 0.40, seller: 0.20, reviews: 0.15, delivery: 0.10, warranty: 0.15 }` | Applied when user does not move sliders |
| `COMPLETENESS_MULTIPLIER` | `{ complete: 1.0, partial: 0.90, unknown: 0.75 }` | Landed-cost confidence factor |
| `HIGHLIGHT_MIN_CONFIDENCE` | `0.7` | Effective-confidence floor for Decision Page recommendations |
| `UPGRADE_MIN_SPEC_GAIN` | `0.25` | Min relative spec improvement for an upgrade alternative |
| `UPGRADE_MAX_COST_INCREASE` | `0.10` | Max relative cost increase for an upgrade alternative |
| `DOWNGRADE_MIN_COST_SAVING` | `0.15` | Min relative cost saving for a downgrade alternative |
| `DOWNGRADE_MAX_SPEC_LOSS` | `0.50` | Max relative spec loss for a downgrade alternative |
| `COMPARABLE_OVERLAP_RATIO` | `0.60` | Min attribute overlap for a different-product alternative |
| `COMPARABLE_SCORE_FLOOR` | `0.85` | Min finalScore ratio vs best-overall for a rival alternative |
| `MAX_ALTERNATIVES` | `3` | Cap on alternative suggestions |
| `STRONG_CRITERION_SCORE` | `0.7` | Criterion score worth stating even when it was not decisive |
| `MAX_DECISIVE_REASONS` | `3` | Cap on criterion reasons in one explanation |

---

## Why this methodology (for the assignment)

| Assignment question | Answer |
| --- | --- |
| Why this methodology? | Weighted multi-criteria scoring: transparent, explainable, user-controllable, handles missing data |
| Which variables? | Landed cost, seller trust, warranty, reviews, delivery — extensible |
| How normalized? | Min–max within the offer set per criterion; bounded 0–1 |
| How weighted? | User-chosen sliders (or published defaults); weights re-normalize per offer for missing criteria |
| How is the final ranking calculated? | `finalScore = confidenceMultiplier × Σ(w × score)`; full list sorted by that score; highlights use lenses on the eligible pool only |
| Missing information? | Criterion excluded for that offer; weights re-normalized; `missingCriteria` in explanation |
| Unreliable information? | `confidenceMultiplier` from `dataConfidence` × completeness; below 0.7 → warning on full list, excluded from highlights |
| How does it explain? | Headline + reasons + caveats (gaps/estimates). Reasons are the criteria with a positive **weighted** margin over the offer this one had to beat, largest first — the actual reason it won, not a list of whatever scored highly |

---

## Pseudocode overview

```python
def decide(offers, near_offers, preferences):
    # Confirmed builds and near-offers are scored TOGETHER, because min-max
    # normalization is relative to the set it is given. Scoring alternatives
    # separately would put them on a different scale from the ranked list.
    all_offers = offers + near_offers

    # 1. Extract raw criterion values
    for offer in all_offers:
        offer.criteria = extract_criteria(offer)

    # 2. Normalize to 0–1, across the whole set
    normalize_criteria(all_offers)

    # 3. Score each offer
    for offer in all_offers:
        available = {c: v for c, v in offer.criteria.items() if v is not None}
        weights = renormalize_weights(preferences.weights, available.keys())
        raw_score = sum(weights[c] * available[c] for c in available)
        offer.confidence = compute_confidence(offer)
        offer.final_score = raw_score * offer.confidence
        offer.missing = [c for c in preferences.weights if c not in available]

    # 4. Split only now that every score is on one scale
    ranked = [o for o in all_offers if o.match_kind == IDENTICAL] or all_offers
    near_ranked = [o for o in all_offers if o not in ranked]

    # 5. Pick highlights from confidence-eligible pool only
    eligible = [o for o in ranked if o.confidence >= HIGHLIGHT_MIN_CONFIDENCE]
    highlights = pick_highlights_by_lens(eligible)

    # 6. Rank alternatives and badge the ones that earn it (no filtering)
    best = max(ranked, key=lambda o: o.final_score, default=None)
    alternatives = select_alternatives(near_ranked, best, confirmed_variant)

    # 7. Build explanations. Peers are passed in so reasons can be stated as
    # margins against the offer this one actually had to beat.
    for label, offer in highlights.items():
        offer.explanation = build_explanation(offer, label, ranked, preferences)

    return DecisionPage(offers=sorted(ranked, key=lambda o: o.final_score, reverse=True),
                        highlights=highlights, alternatives=alternatives)
```

Implementation in Python (dataclasses, no heavy ML needed for v1). The ranking engine, explanation builder, and alternative scout are separate modules matching the architecture's service boundaries.
