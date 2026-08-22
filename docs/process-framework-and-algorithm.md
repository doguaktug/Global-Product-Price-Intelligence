# Process framework and suggested algorithm

This document has two parts. Part 1 describes the process from user input to the Decision Page. Part 2 describes the algorithm that scores offers and explains the result.

The system answers one question: which offer is the best purchase for this user, and why?

## Terms used in this document

| Term | Meaning |
| --- | --- |
| catalog | A small reference of valid products. It does not store live prices. |
| offer | One listing from one source when the system collects it. |
| list price | The original sticker price in the source currency. The system never overwrites it. |
| landed cost | The estimated total in the reference currency after FX, shipping, tax, and fees. |
| confirmed variant | The exact product the user confirmed, or the single valid product the catalog already identified. |
| Decision Page | The result screen. It shows highlights, reasons, and close alternatives. |

---

# Part 1: Process framework

## STEP 1 – User input

The first page has three controls:

- a search bar
- weight sliders
- destination country and reference currency

The user types the product in the search bar. The name does not need to be exact.

The sliders set how much each criterion matters. The criteria include price, seller reliability, and warranty. The user may also set reviews, delivery, and specifications.

The sliders, the country, and the currency are optional. If the user does not change them, the system applies published defaults.

Default country is TR. Default currency is TRY.

Country and currency follow a waterfall. Each later step replaces the step before it:

1. Default: TR and TRY.
2. If the user permits location, the system may replace the default with the inferred country and its usual currency.
3. If the user selects a country or a currency, that choice replaces the default and the location guess.

Search uses the values that apply when the user submits.

Published default weights:

- price: 0.50
- seller: 0.25
- reviews: 0.15
- delivery: 0.10

These weights sum to 1. They do not include warranty or specifications. The user can add those criteria with the sliders.

The user may change the weights later. The system then ranks the same offers again. It does not collect the offers again.

## STEP 2 – Compare the search with the catalog

The system compares the user input with the catalog. The catalog holds brands, model families, and valid technical options.

The system extracts category, brand, model, and technical attributes from the input. It also corrects spelling errors.

If the input is missing, invalid, or ambiguous, the system shows one or more popups. The user then selects a valid option.

The system shows a popup when:

- The typed option is not valid. Example: 600 GB when the model has 512 GB and 1 TB.
- More than one catalog product still matches.
- A required attribute is missing. Example: storage or RAM.
- The user may mean a model family rather than one exact product.
- Parse confidence is low.

If the catalog already has one unique valid product, the system does not show a popup.

The system does not invent a product variant.

## STEP 3 – Collect live offers

After the product is known, the system shows a loading screen. The system does not add a fake delay.

During this step the system collects current offers from worldwide sources. It prefers official APIs. It uses a page parse or a headless browser only when an API is not available. The source terms must also allow that method.

Each source has its own adapter. If one source fails, the other sources continue.

The catalog is not a price database. This live collection supplies prices, stock, and shipping quotes.

## STEP 4 – Match offers to the confirmed product

The same physical product can appear under different titles. The system matches each offer to the confirmed variant.

The system uses identity codes first (EAN, UPC, GTIN, model code). It then uses brand, model, RAM, storage, and other attributes. It uses title text only when identity and attributes are not enough.

Each offer receives one match kind:

| Match kind | Meaning | Use |
| --- | --- | --- |
| identical | Same model and same critical specs | Main ranking set |
| similar | Same family, different specs (example: 512 GB vs 1 TB) | Alternative candidate |
| different | Another product in the same category | Alternative candidate, with stricter rules |
| unmatched | No reliable match | Exclude |

The system does not merge a similar SKU into the identical set.

The system excludes out-of-stock offers from ranking. Offers with unknown stock remain in the set. They carry lower confidence.

## STEP 5 – Convert currency

The system keeps the original list price. It does not replace that amount.

A live FX provider converts each list price into the reference currency. The Decision Page shows the original amount, the rate, the rate time, and the converted amount.

## STEP 6 – Calculate landed cost

List price in a common currency is not enough for worldwide offers. After FX, the system estimates the total cost to the user destination:

```
landed cost ≈ converted list price + shipping + border tax + registration fees + other known fees
```

If the source quotes shipping, the system uses that quote. If not, it uses a transparent estimate and labels it as estimated.

If a fee cannot be estimated with enough reliability, the system marks the landed cost as partial or unknown. It does not pretend precision.

The ranking step prefers landed cost, not list price, when it compares offers across countries.

## STEP 7 – Run the suggested algorithm

The pipeline now holds matched offers, converted prices, and landed costs. It also holds the user weights from STEP 1.

The ranking algorithm then:

1. Extracts criterion values from each offer.
2. Scales those values to a common 0–1 range.
3. Handles missing and unreliable data.
4. Calculates a weighted score.
5. Selects highlights (lowest list price, lowest total cost, best for you, and others).
6. Selects close alternatives under guardrails.
7. Writes a plain-language explanation for each highlight and each alternative.

Part 2 specifies these steps.

## STEP 8 – Show the Decision Page

The Decision Page is the result. It shows:

- the recommended offers and why they won
- original price and original currency
- FX rate, rate time, and amount in the reference currency
- landed-cost add-ons (shipping, tax, duty), with estimates marked
- seller, reliability, stock, delivery, warranty, and returns
- specification differences versus the confirmed variant
- highlight cards and close alternatives, each with a reason
- a freshness time on every card (example: price seen 3 min ago)

When the user clicks a retailer link, the system does a quick check of that listing. If the item is gone or the price changed by a material amount, the system warns the user. If the check fails or exceeds the time limit, the system still redirects. It states that the user must confirm the listing on the retailer page.

---

# Part 2: Suggested algorithm

The assignment asks which option is the best purchasing decision, and why. Price is one factor. A user may care about warranty, seller trust, delivery speed, or specifications.

A cheaper list price in another country may have a higher landed cost. Missing information must lower confidence. The algorithm must not ignore gaps.

The algorithm must:

1. Scale mixed criteria to a common range.
2. Apply the user weights, or the published defaults.
3. Handle missing and unreliable data without hiding the gaps.
4. Produce a score and a plain-language explanation.
5. Select highlights and guarded alternatives.

Implementation language is Python.

## Inputs

| Input | Source |
| --- | --- |
| `offers[]` | Offers with match kind identical for the confirmed variant |
| `nearOffers[]` | Offers with match kind similar or different |
| `preferences` | User weights, destination country, and reference currency |

Each offer already has list price, converted list price, landed cost, seller, stock status, and delivery time. It also has warranty, return policy, raw specifications, data confidence, and match kind.

## Algorithm STEP 1 – Extract criterion values

For each offer, the system extracts raw values:

| Criterion | Raw value | Source |
| --- | --- | --- |
| price | Landed cost total in the reference currency | `landedCost.total.amount`. If landed cost is not available, use the converted list price. |
| seller | Seller reliability | `seller.reliability` (0–1) multiplied by `source.reliability` (0–1) |
| warranty | Warranty strength | Warranty duration in months. Official or manufacturer warranty ranks above third-party warranty. |
| specs | Specification closeness | How close this offer's specs are to the confirmed variant, or how far they exceed it |
| reviews | User review score | Rating on a 0–1 scale (example: 4.5/5 → 0.9). A low review count reduces the score. |
| delivery | Delivery speed | Estimated days to the destination. In-stock status adds a bonus. |

Not every offer has every field. Algorithm STEP 3 handles that case.

## Algorithm STEP 2 – Scale values to a 0–1 range

Each criterion must use the same 0–1 scale before the system applies weights. Direction matters. A lower price is better. A higher rating is better.

The system scales each criterion with min–max inside the current offer set.

For lower-is-better criteria (price, delivery days):

```
score_i = (max_value - value_i) / (max_value - min_value)
```

For higher-is-better criteria (seller, warranty, specs, reviews):

```
score_i = (value_i - min_value) / (max_value - min_value)
```

If all values for a criterion are the same, every offer scores 1.0 on that criterion. That criterion does not separate the offers.

Why min–max:

- The scale stays between 0 and 1. A score of 0.85 reads as 0.85 out of 1.
- The best offer in the set scores 1. The worst offer scores 0.
- The method works with a small set. A search has at most about 20–30 offers.

## Algorithm STEP 3 – Handle missing data

Real offers have gaps. The algorithm does not guess missing values. It penalizes gaps. It discloses them.

| Situation | Rule |
| --- | --- |
| A criterion value is missing for this offer | Exclude that criterion from this offer’s score. Scale the remaining weights so they sum to 1. |
| A criterion value is missing for all offers | Drop the criterion from this ranking round. Scale the weights so they sum to 1. |
| Landed-cost completeness is partial or unknown | Apply a confidence penalty. |
| Data confidence is low | Apply a confidence penalty. |
| Specs conflict across sources for the same offer | Use the source with higher reliability. Record the conflict. |

### Scale the remaining weights (per offer)

Example. The user weights are price 0.50, seller 0.25, warranty 0.15, specs 0.10. This offer has no warranty.

- Drop warranty. The remaining weights are price 0.50, seller 0.25, specs 0.10. The sum is 0.85.
- Scale again: price 0.588, seller 0.294, specs 0.118.

The system does not remove the offer. The offer competes with less information. The field `missingCriteria` records what was absent. The explanation uses that list.

### Confidence penalty

```
effectiveScore = finalScore × confidenceMultiplier
```

```
confidenceMultiplier = dataConfidence × completenessMultiplier
```

| Completeness | Multiplier |
| --- | --- |
| complete | 1.00 |
| partial | 0.90 |
| unknown | 0.75 |

A landed cost marked partial slightly lowers the offer. Unknown is a stronger penalty. The explanation states the cause. Example: total cost is estimated because shipping or duties are not fully confirmed.

## Algorithm STEP 4 – Calculate the weighted score

```
finalScore = confidenceMultiplier × sum(w_c × score_c)
```

`w_c` is the scaled weight for each criterion that is present. `score_c` is the 0–1 value from Algorithm STEP 2.

Example with all default criteria present:

```
FinalScore = confidence × (
    0.50 × PriceScore
  + 0.25 × SellerScore
  + 0.15 × ReviewScore
  + 0.10 × DeliveryScore
)
```

## Algorithm STEP 5 – Select highlights

After the system scores the offers, it selects highlights. Highlights are not only a sort by final score. Each highlight uses its own rule.

| Highlight | Selection rule |
| --- | --- |
| Lowest list price | Minimum converted list price. Ignore fees. |
| Lowest total cost | Minimum landed cost among offers with completeness complete |
| Best seller / trust | Maximum seller score (seller reliability × source reliability) |
| Best warranty | Maximum warranty score |
| Best specification | Maximum spec score versus the confirmed variant |
| Best for you | Maximum final score under the user weights |

One offer may hold more than one highlight label. Example: lowest total cost and best for you.

If lowest total cost and lowest list price are the same offer, show that offer once with both labels. Do not duplicate the card.

## Algorithm STEP 6 – Select close alternatives

Candidates are offers in `nearOffers[]` with match kind similar or different.

The system does not suggest random similar titles. Each alternative must pass a guardrail. A guardrail is a test that an alternative must pass before the system shows it.

### 6a. Same-family specification variants

These offers share the family. Specs differ. Example: 512 GB versus 1 TB.

Value test for an upgrade:

```
specGainRatio = specImprovement / baseSpec
costGainRatio = costIncrease / baseLandedCost

isGoodUpgrade = specGainRatio >= 0.25
            and costGainRatio <= 0.10
```

The defaults mean a spec jump of at least 25% and a cost increase of at most 10%.

Value test for a downgrade:

```
costSavingRatio = costSaving / baseLandedCost
specLossRatio   = specLoss / baseSpec

isGoodDowngrade = costSavingRatio >= 0.15
              and specLossRatio  <= 0.50
              and the offer still meets the confirmed minimum requirements
```

The defaults mean a cost saving of at least 15% and a spec loss of at most 50%.

These thresholds are configurable. The numbers above are the published defaults.

### 6b. Comparable different products

These offers have match kind different. They remain in the same category. They have a comparable form factor.

```
isComparable = sameCategory
           and attributeOverlapRatio >= 0.60
           and finalScore > bestOverall.finalScore × 0.85
```

The offer must share at least 60% of the core spec keys. Its final score must be greater than 85% of the best-for-you score.

### 6c. Selection and cap

1. Collect all candidates that pass 6a or 6b.
2. Prefer diversity of reason: one upgrade, one downgrade, and one rival, rather than three similar suggestions.
3. Cap the list at 3 alternatives.
4. Give each alternative its own explanation. State what differs, the cost delta, and why it could beat the primary pick.
5. If no candidate passes the guardrails, show zero alternatives.

## Algorithm STEP 7 – Write the explanation

The system writes a plain-language explanation for every highlight and every alternative before the Decision Page appears.

The explanation has three parts:

- **Headline:** the highlight label and the single strongest reason.
- **Reasons:** each criterion where this offer scored at least 0.7. Also include the factor that decided the result versus the runner-up. Include the raw value and the context.
- **Caveats:** each missing criterion, each partial or unknown landed-cost line, and any low data confidence.

The explanation must not be a dump of scores. It must read as a short purchasing argument.

Extra rules:

1. If the offer is best for you but not the cheapest, state why the cheaper option lost. Example: Offer X is 2,100 TL cheaper, but it has no warranty and an unknown seller.
2. For an alternative, state what differs, the landed-cost delta, and the value-test result.

Example shape:

```
headline: Lowest total cost because shipping from DE is low.
reasons:
  - landed cost: lowest total at 28,450 TL (the list price was not the cheapest)
  - seller: authorized retailer, reliability 0.92
caveats:
  - import duty is estimated (±5%). Final customs may differ.
  - review count is low (12 reviews). The score may shift.
```

## Configurable parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_WEIGHTS` | price 0.50, seller 0.25, reviews 0.15, delivery 0.10 | Applied when the user does not change the sliders |
| `COMPLETENESS_MULTIPLIER` | complete 1.0, partial 0.90, unknown 0.75 | Landed-cost confidence penalty |
| `UPGRADE_MIN_SPEC_GAIN` | 0.25 | Minimum relative spec gain for an upgrade alternative |
| `UPGRADE_MAX_COST_INCREASE` | 0.10 | Maximum relative cost increase for an upgrade alternative |
| `DOWNGRADE_MIN_COST_SAVING` | 0.15 | Minimum relative cost saving for a downgrade alternative |
| `DOWNGRADE_MAX_SPEC_LOSS` | 0.50 | Maximum relative spec loss for a downgrade alternative |
| `COMPARABLE_OVERLAP_RATIO` | 0.60 | Minimum attribute overlap for a different-product alternative |
| `COMPARABLE_SCORE_FLOOR` | 0.85 | Minimum final-score ratio versus best-for-you |
| `MAX_ALTERNATIVES` | 3 | Cap on alternative suggestions |

## Procedure summary

1. Extract raw criterion values for each identical offer.
2. Scale each criterion to 0–1 inside this offer set.
3. For each offer, drop missing criteria. Scale the remaining weights so they sum to 1.
4. Calculate the weighted sum. Multiply by the confidence multiplier.
5. Select highlights by these rules, not only by final score.
6. Test similar and different offers against the upgrade, downgrade, and rival guardrails. Keep at most 3.
7. Write a headline, reasons, and caveats for each highlight and each alternative.
8. Return the Decision Page.

The ranking engine, the explanation builder, and the alternative scout are separate modules. This matches the service boundaries in the architecture.
