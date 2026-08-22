# Process framework and suggested algorithm

This document describes the path from the first page to the Decision Page. It also describes the ranking method that the process uses when it has a set of offers.

The system answers one question: which offer is the best purchase for this user, and why?

---

## STEP 1 – User input

The first page has:

- a search bar
- sliders for the weights of the search (reliability, price, warranty)
- country and currency

The sliders, the country, and the currency are optional. If the user does not set them, the system applies default values.

Default country is TR. Default currency is TRY. If the user permits location, the system may replace that default with the inferred country and its usual currency. If the user selects a country or a currency, that choice replaces the default and the location guess.

Default weights are price 0.50, seller 0.25, reviews 0.15, and delivery 0.10. The user can also raise warranty or specifications with the sliders. The weights always sum to 1.

The user types a product in the search bar. The name does not need to be exact.

## STEP 2 – Match the search with the catalog

The system compares the input with the catalog. The catalog holds valid brands, models, and technical options. It does not hold live prices.

If the input is missing, wrong, or unclear, the system shows a popup. The user selects a valid option. Example: the user typed 600 GB, and the model only has 512 GB and 1 TB.

If the catalog already has one valid product for this input, the system does not show a popup. The system does not invent a product.

## STEP 3 – Collect live offers

After the product is known, the system shows a loading screen. The wait is real collection work. The system does not add a fake delay.

The system collects current offers from sources in several countries. It prefers official APIs. If one source fails, the other sources continue.

## STEP 4 – Match offers to the product

The same product can appear under different titles. The system checks each offer against the confirmed product.

- Identical: same model and same critical specs. These offers enter the ranking set.
- Similar: same family, different specs (example: 512 GB vs 1 TB). These offers may become alternatives.
- Different: another product in the same category. These offers may become alternatives only if they pass a later test.
- Unmatched: no reliable link. The system drops these offers.

The system does not treat a similar SKU as the same offer. It also drops offers that are out of stock.

## STEP 5 – Convert currency and calculate landed cost

The system keeps the original list price. A live FX rate converts that price into the reference currency. The Decision Page shows the original amount, the rate, and the rate time.

List price is not the full cost for a foreign offer. After FX, the system adds shipping, border tax, and other destination fees. That sum is the landed cost.

If a fee is not known with enough reliability, the system marks the total as partial or unknown. It does not invent a precise number.

## STEP 6 – Score the offers

This is the suggested algorithm. Price alone does not decide the result. A cheaper list price may have a higher landed cost. A user may also care about seller reliability, warranty, reviews, or delivery.

For each identical offer, the system reads:

- price, as landed cost (or converted list price if landed cost is missing)
- seller reliability
- warranty
- specification closeness
- reviews
- delivery time

It then scales each of those values to a 0–1 range inside this search. A lower price gets a higher score. A higher rating gets a higher score. The best offer in the set scores 1 on that criterion. The worst scores 0.

The user weights from STEP 1 then combine the scores:

```
final score = confidence × (w_price × price score + w_seller × seller score + other weighted scores)
```

Only the criteria that exist for that offer enter the sum. If warranty is missing, the system drops that weight and scales the rest so they still sum to 1. The offer stays in the ranking. The explanation later states what was missing.

Unreliable data also lowers the score. Partial landed cost multiplies the result by 0.90. Unknown landed cost multiplies it by 0.75. Low source confidence lowers it further.

## STEP 7 – Select highlights and close alternatives

The system does not only sort by the final score. It also labels offers by separate tests:

- lowest list price
- lowest landed cost (only among complete cost estimates)
- best seller
- best warranty
- best specification
- best for you (highest final score under the user weights)

One offer may receive more than one label. If two labels point to the same offer, the page shows that offer once.

Close alternatives come from the similar and different offers in STEP 4. They are not random similar titles.

A same-family upgrade is shown only if the spec gain is at least 25%. The extra landed cost must be at most 10%. A downgrade is shown only if it saves at least 15%. The spec loss must be at most 50%, and the offer must still meet the confirmed minimum.

A different product is shown only if it shares the category and overlaps at least 60% of the core specs. Its score must be greater than 85% of the best-for-you score.

The system keeps at most three alternatives. It prefers one upgrade, one downgrade, and one rival. If no candidate passes these tests, it shows none.

## STEP 8 – Explain the result and show the Decision Page

Before the page appears, the system writes a short reason for each highlight and each alternative.

The reason has a headline, the decisive facts with their values, and any caveats. Caveats cover missing fields, estimated fees, and low confidence. If the best-for-you offer is not the cheapest, the text states why the cheaper offer lost.

The Decision Page then shows those cards. It also shows the original price, the FX rate and time, the landed-cost add-ons, and a freshness time on every offer. When the user opens a retailer link, the system checks that listing again. If the item is gone or the price changed by a large amount, it warns the user.
