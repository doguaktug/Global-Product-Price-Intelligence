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

Default weights are price 0.40, seller 0.20, reviews 0.15, delivery 0.10, and warranty 0.15. The weights always sum to 1.

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

If conversion fails for one offer (unsupported currency, FX provider error), that offer is dropped and the rest of the search continues. If every remaining offer fails conversion — or the search otherwise ends with no usable offers (no sources, none in stock, none matched) — the session is marked failed and the caller gets the reason instead of an empty Decision Page.

List price is not the full cost for a foreign offer. After FX, the system adds shipping, border tax, and other destination fees. That sum is the landed cost.

If a fee is not known with enough reliability, the system marks the total as partial or unknown. It does not invent a precise number.

## STEP 6 – Score the offers

This is the suggested algorithm. Price alone does not decide the result. A cheaper list price may have a higher landed cost. A user may also care about seller reliability, warranty, reviews, or delivery.

For each identical offer, the system reads five criteria:

- **price**, as landed cost (or converted list price if landed cost is missing)
- **seller**, mostly the seller's own rating with the hosting site's reputation counting for 30% of it
- **reviews**, from how many reviews the seller has, on a log scale so the first hundred matter more than the next thousand
- **delivery time**, parsed into days
- **warranty**, parsed into months

The system does not score specs. Every offer in this set already matched the confirmed variant exactly, so their specs are the same and a spec score would be 1 for all of them. Specs decide which offers get in here, and which alternatives are worth showing — not the order inside the set.

Delivery and warranty arrive as text each source writes its own way, in its own language: "2-4 Werktage", "1-3 iş günü", "Next day", "24 months". The system parses these into days and months. A range counts as its slow end, because that is what the buyer waits for. If the text has no unit at all — a bare "48", or "manufacturer warranty" with no length — the system treats the criterion as missing rather than guessing a number.

It then scales each of those values to a 0–1 range inside this search. A lower price gets a higher score, and so does a faster delivery. A higher rating gets a higher score. The best offer in the set scores 1 on that criterion. The worst scores 0.

The user weights from STEP 1 then combine the scores:

```
final score = confidence × (w_price × price score + w_seller × seller score + other weighted scores)
```

Only the criteria that exist for that offer enter the sum. If warranty is missing, the system drops that weight and scales the rest so they still sum to 1. The offer stays in the ranking. The explanation later states what was missing.

Because the weights are always rescaled this way, only their **proportions** matter. Pushing every slider to the maximum gives the same ranking as leaving them all low. Setting a weight to zero removes that criterion from the round completely.

Unreliable data also lowers the score. Partial landed cost multiplies the result by 0.90. Unknown landed cost multiplies it by 0.75. Low source confidence (lesser-known sites, sparse reviews) lowers it further.

## STEP 7 – Select highlights and close alternatives

The system does not only sort by the final score. It also labels offers by separate tests — **but only among offers whose effective confidence is at least 0.7**. A cheaper low-confidence listing can still sit at the top of the full ranked list with a reliability warning; it is not shown as a Decision Page recommendation.

Highlight lenses on the eligible pool:

- best for you (highest final score under the user weights) — selected first
- lowest list price
- lowest landed cost (among usable cost estimates)
- best seller
- best warranty (longest parsed warranty among eligible offers)

If “best for you” is the same offer as another lens, that other highlight is dropped. Specs do not vary across identical-product offers, so there is no “best specification” highlight. AlternativeScout still compares specs when it picks close (same-family) or far (comparable product) alternatives.

Close alternatives come from the similar and different offers in STEP 4. They are not random similar titles.

They are scored in the **same pass** as the confirmed offers and only separated afterwards. This matters because the 0–1 scale in STEP 6 is stretched to fit whatever set it is handed, so an alternative scored on its own would carry a number calibrated against other alternatives. Its score would not be comparable to the ranked list, and the rival test below compares exactly those two numbers. Alternatives are then sorted by final score, like the main list.

The value tests decide which alternatives get a **badge**, not which ones are shown. A same-family variant is badged an upgrade if some spec gains at least 25% while the landed cost rises at most 10%. It is badged a downgrade if it saves at least 15% while no spec falls more than 50% and it still meets the confirmed minimum. A different product is badged a rival if it shares the category, overlaps at least 60% of the core specs, and scores above 85% of the best-for-you score.

Spec gain and loss are measured across every numeric spec in the category, not one chosen field, so a machine that doubles its memory while keeping its storage counts as an upgrade.

An alternative that passes no test still appears, ranked, and says in its caveats that it does not clear a value test. The badge is a claim about value and has to be earned; simply existing as an option does not. Hiding the option instead would leave the user with an empty panel and no stated reason.

The system keeps at most three alternatives and fills those slots with one upgrade, one downgrade, and one rival where it can, before topping up with the highest-scoring unbadged candidates — three cheaper-but-smaller variants would tell the user one thing three times. Each alternative also carries its landed cost **minus the top pick's**, so a negative figure means it is cheaper; the panel exists to answer what switching would cost, and a reader should not have to subtract two totals to find out. If there are no similar or different offers at all, the panel is empty.

## STEP 8 – Explain the result and show the Decision Page

Before the page appears, the system writes a short reason for each highlight and each alternative.

The reason has a headline, the decisive facts with their values, and any caveats.

A stated reason has to be the reason the offer actually won. For each criterion the system takes the offer's weighted contribution — its slider weight times its 0–1 score — and subtracts the same figure for the offer it had to beat: second place for the winner, or the offer directly above for anyone further down the list. Criteria where that margin is positive are the reasons, largest margin first, and at most three are shown. The weighting matters because a criterion the user set to 5% cannot be the reason for anything, however well the offer scored on it; and comparing against the immediate rival rather than the whole field stops a criterion that only beats a few weak listings from looking decisive. A criterion the offer scores well on but ties or loses is not offered as a reason it won, because it was not one. Such criteria are appended afterwards only if there is room, so a strong all-rounder still reads as one.

Caveats cover missing fields, estimated fees, and low confidence. If the best-for-you offer is not the cheapest, the text names the cheapest offer it beat and the criteria that offer lost on.

The Decision Page then shows those cards. It also shows the original price, the FX rate and its publication date, the landed-cost add-ons, and the time each offer was collected. Retailer links open directly. Whether an offer can actually be bought was settled during the search — an out-of-stock listing never entered the ranking, and one with unknown stock was scored down for it — so there is no second check when the user clicks through.
