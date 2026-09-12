# Product Price Intelligence — System Architecture

Personal project draft. Full decision-support model (not only a ranking engine).

**Core idea:** The user enters a product **and selects preference weights** (or accepts defaults); the system understands and normalizes the query, **confirms only when the catalog match is missing or ambiguous**, then pulls live worldwide offers. Prices are converted with current FX, **landed costs** (shipping, border tax, registration fees) are added, offers are matched, ranked by user preferences, and presented with **explicit reasoning** — including carefully selected close alternatives.

## High-level components

| Component | Responsibility |
| --- | --- |
| Frontend | Welcome search, weight sliders, country/currency waterfall, confirm popup, loading, Decision Page |
| API / Orchestrator | Owns one search session; coordinates services and human-in-the-loop confirmation |
| Query Normalizer | Typos, category, brand, model, capacity and other attributes |
| Reference Catalog | Small reference data for normalization / validation (not a price warehouse) |
| Confirmation Gate | Popup on search when the catalog match is missing, invalid, or ambiguous |
| Live Data Acquisition | API / scraping / headless browser adapters per source |
| Product Matching | Catalog identifiers, then normalized attributes; same product vs near variant |
| FX Service | Live exchange rates into a common currency |
| Landed Cost Layer | Shipping, border/import tax, registration and similar destination fees |
| Ranking Engine | User weights + total cost + trust + reviews (+ delivery signals) |
| Explanation Builder | Why each highlighted choice won (or lost) before presentation |
| Alternative Scout | Same-product different specs, or different but comparable products |
| Result Presentation | Five highlight lenses (best for you, lowest list price, lowest total landed cost, most trusted seller, best warranty) and close alternatives — each with rationale |

## Architectural principles

- Prices are **not** pre-filled into a giant database. On search, fetch as-current data via API, scraping, or headless browser where appropriate.
- Keep a **small reference catalog**: brand, model family, category, and valid technical options for normalization — not “the whole product internet.”
- Variable fields (price, stock, shipping quotes, fees) are acquired at query time. Short-lived cache is optional later.
- First scope: **phone, laptop, tablet**.
- Prefer official APIs when available; each retailer/source gets its own adapter. Respect rate limits, ToS, robots.txt, and data licenses.

---

## End-to-end process flow

```
1. User query
2. User selects preference weights     ← sliders optional; country/currency waterfall
3. Normalize (+ catalog validation)
4. Confirm popup only if needed        ← skip when catalog match is unique and valid
5. Loading screen                      ← animation / fun facts over real work
6. Live worldwide acquisition
7. Product matching (same vs near)
8. FX conversion
9. Landed cost (shipping, tax, fees)
10. Ranking by those weights
11. Build explanations / reasoning
12. Scout close alternatives (careful rules)
13. Decision Page
```

Screens: **Welcome → (confirm popup) → Loading → Decision Page.** See [ui-concept.md](ui-concept.md). The STEP writeup of this process is [process-framework-and-algorithm.md](process-framework-and-algorithm.md).

### 1. User query

The user types what they want to buy (need not be a perfect product name).

### 2. User selects preference weights

This is a first-class user step, not a hidden ranking default. On the **welcome / search** screen, next to the search bar:

- **Criterion weights** (sliders) over the five scored criteria: landed price, seller trust, reviews, delivery, warranty. Specs are not among them — only offers matching the confirmed variant exactly are ranked, so their specs are identical and cannot separate them. The sliders are **relative**: the engine re-normalizes them, so they do not have to sum to 1 and each can move on its own
- **Destination country** and **reference currency** — optional controls next to the sliders
- Optional **catalogue browse** if they want to explore instead of typing

If they do not move the sliders, **published defaults** apply (price 40%, seller 20%, reviews 15%, delivery 10%, warranty 15%) and the session still records `UserPreferences` — ranking never invents weights after the fact. The user can change weights later and re-rank without re-fetching offers.

**Country / currency waterfall** (each later step overwrites the one before):

1. **Default:** TR + TRY — **implemented**, recorded as `origin = default`
2. **If they permit geolocation:** inferred country + that country’s usual currency replaces the default — **proposed, not implemented**
3. **If they manually select** next to the sliders: that replaces default or geo — **implemented**, recorded as `origin = manual`

Search uses whatever is in effect at submit. Manual choice is not snapped back to location.

`UserPreferences.origin` records which step last set the country, so the Decision Page can say "we assumed Türkiye" instead of implying the user chose it. Only `default` and `manual` are reachable today; nothing in the code infers a country from an IP or a browser API, so nothing claims `geolocation`. The enum member exists because that step is a designed part of the waterfall rather than a hypothetical, and an explicitly supplied origin is never overwritten — a geolocation step can set its own when it is built.

### 3. Understand and normalize

The user does not need a perfect product name (`Aple`, wrong capacity, etc.). The normalizer extracts category, brand, model, and technical attributes; fixes typos; and checks against valid catalog options.

The same parser is reused on **listing titles** during matching (step 7), because a title is the same kind of string as a query — free text naming a build. Keeping one implementation means a query and a listing can never disagree about what `"512GB 12GB RAM"` means.

Separately, a **unit parser** normalizes measurement specs that sources write in their own units and punctuation: `5,000 mAh`, `5.000 mAh`, `5000mAh` and `5 Ah` are one battery, and `6.9"`, `6,9 inç`, `6.9型` and `17,5 cm` are one screen. See [data-model.md](data-model.md#normalizedspec) for the canonical unit per key and the disambiguation rules.

Colors and other non-core, frequently changing fields need not live in the catalog. The catalog answers “what product could this be?” Live data answers “where, how much, under what conditions — now?”

### 4. Confirm model / specs (popup on search)

**Not a separate page.** If they try to search a product that is not real or is ambiguous, show a **popup on the welcome screen**. If normalization yields a **single valid** `ProductVariant`, skip the popup and go to loading.

**Do not silently invent variants.**

Example: catalog has 512 GB and 1 TB, user typed “600 GB”:

> 600 GB isn’t a valid option for this model. Are you looking for **512 GB** or **1 TB**?

Also popup when:

- brand/model is fuzzy and multiple catalog candidates remain
- required comparison attributes are missing (e.g. storage or RAM for phones/laptops)
- the user may mean a model family rather than a specific SKU
- parse confidence is low even if one candidate is guessed

After they confirm (or if no popup was needed), show the loading screen and start live acquisition.

### 5. Loading screen

Shown while acquisition, FX, landed cost, ranking, and explanations run. Fun animation and/or rotating fun facts over **real** work — do not add fake delay. Optional “still waiting on …” if a source is slow.

### 6. Live data acquisition

Official/structured APIs first; each source is a separate adapter. HTML or headless only when an API is not available **and** terms allow it. Worldwide sources may return prices in different currencies and under different commercial terms.

See [data-source-strategy.md](data-source-strategy.md) for MVP countries, source mix, FX/fee providers, reliability, and legal limits.

### 7. Product matching

Same physical product can appear under different titles across stores.

Matching is **two tiers, tried in order**:

| Tier | Logic | Strong when |
| --- | --- | --- |
| 1. Identity | EAN/UPC/GTIN, manufacturer model code, per-source retailer SKU | Strong IDs exist across listings |
| 2. Attributes | Category identity keys — storage, RAM, region, chip, connectivity — compared against catalog variants | Phones / laptops / tablets, where the build is what distinguishes one SKU from another |

An **absent** identifier is missing evidence, not contradicting evidence: when tier 1 finds nothing, matching falls through to tier 2 rather than rejecting the offer. Only a *stated conflict* rules a variant out. This is what makes marketplace listings usable at all — eBay publishes no identifier the catalog shares, so every eBay offer is decided by tier 2.

Tier 2 needs structured attributes, and most sources do not publish any. The attributes therefore come from **normalizing the listing title** with the same parser that reads user queries (see step 5): `"Galaxy S26 Ultra 512GB 12GB RAM EU Black"` yields `storage_gb=512, memory_gb=12, region_version=EU, colour=Black`. Where a source does publish structured specs, those are preferred and passed through the unit parser first.

There is deliberately **no free-text similarity tier**. Fuzzy title scoring is used to pick the product *family* from the user's query (step 5), where a wrong guess only opens a confirmation popup. Using it to decide which *build* an offer is would silently merge a 256 GB listing with a 512 GB one, and a wrong answer there corrupts the price comparison itself. When the two tiers cannot decide, the offer is `unmatched` and excluded, which is the honest outcome.

Matching must distinguish:

- **Exact same product** (same model + same critical specs)
- **Same model, different specs** (e.g. 256 GB vs 512 GB) — candidate for “close alternative,” not a merged offer
- **Different but comparable product** — also alternative territory, with stricter rules

### 8. FX conversion

Convert offer list prices into a common currency via a live exchange-rate provider (no custom FX engine). Example: USD/EUR offers → TRY (or user’s preferred currency) using current rates, then pass amounts into landed-cost and ranking on the same scale. If conversion fails for a single offer, drop that offer and continue ranking the rest. If every offer is dropped (conversion or another pipeline step), fail the search with a reason instead of returning an empty Decision Page.

An offer already priced in the reference currency is **not converted** — the quote records `rate = 1`, `provider = identity` and no rate date, and explanations say so instead of quoting a meaningless rate. The timestamp shown with a real conversion is the provider's **publication date** for that rate (ECB publishes daily), not the moment we fetched it; listing freshness is a separate field, `Offer.collectedAt`. See [data-model.md](data-model.md#fxquote).

Flat fees inside landed cost are authored in USD (`FEE_CURRENCY`) and restated into the reference currency through the same FX service, so a shipping or registration figure is never silently read as "450 EUR" for a user pricing in EUR. A fee already in the reference currency skips conversion.

### 9. Landed cost (after FX)

For **worldwide** options, list price in common currency is not enough. After FX, compute an estimated **total landed cost** toward the user’s destination:

- **origin VAT removed** — a foreign sticker usually includes the seller's local VAT, which an export sale does not charge
- shipping / delivery on the specific **origin→destination lane**
- border / import / VAT / duty estimates where applicable
- registration or other mandatory destination fees when relevant to the category/region

```
Net         = FX(ListPrice) − OriginVAT
LandedCost ≈ FX(ListPrice) − OriginVAT + Shipping(origin→destination) + Duty(Net) + VAT(Net + Shipping + Duty) + RegistrationFees
```

Rules of thumb for the prototype:

- **Shipping depends on both ends of the journey.** DE→TR is a short regional hop and JP→TR is long-haul, so a per-destination figure was wrong in both directions. Lanes are curated fixtures (`data/fixtures/shipping_lanes.json`) scaled by category parcel size; a carrier rate API or per-source published shipping is the intended replacement.
- **Do not tax the buyer twice.** Strip the origin country's VAT before applying destination duty and VAT, and show the removal as its own visible (negative) line so the breakdown still adds up to the total.
- Keep fee breakdowns visible in explanations (so “cheaper list price, higher landed cost” is understandable).
- **Distinguish a rate we looked up from a rate we invented.** If a lane or rate is genuinely unavailable, mark that line `unavailable`, set `completeness` to `unknown`, and let the confidence multiplier down-rank the offer — do not pretend precision.

Ranking prefers **landed cost**, not raw list price, when comparing across countries. List price keeps a lens of its own (`lowest_list_price`) so the user can see the difference the border made, but it is not what the score is built on.

### 10. Ranking with user preferences

Goal is not only cheapest list price — it is the best fit for this user. Use the **weights already chosen in step 2** (including defaults if the user left them unchanged), e.g.:

- price (landed) 40%
- seller trust 20%
- review score 15%
- delivery 10%
- warranty 15%

Normalize each criterion to a comparable scale, then:

```
FinalScore = confidenceMultiplier × (
    w_price × PriceScore
  + w_seller × SellerScore
  + w_review × ReviewScore
  + w_delivery × DeliveryScore
  + w_warranty × WarrantyScore
)
```

Lower landed cost → higher PriceScore, and fewer delivery days likewise. `SellerScore` blends the seller's own rating with the reputation of the hosting site, the site counting for 30% so a strong seller on a mid-tier marketplace is not capped by it. `ReviewScore` comes from review volume on a log scale. Delivery and warranty are parsed out of each source's own free text (`"2-4 Werktage"`, `"24 months"`); text with no usable unit makes the criterion missing rather than a guessed number.

Uncertain landed-cost offers carry a confidence multiplier. Offers below the **0.7** effective-confidence floor stay in the ranked list with a warning but are excluded from highlight recommendations. Missing criteria are excluded per offer with weight re-normalization.

Full algorithm: [proposed-algorithm.md](proposed-algorithm.md) — normalization, missing-data rules, confidence, highlight selection, alternative guardrails, and explanation generation.

### 11. Explanation / reasoning (before presentation)

Before the UI shows winners, an **Explanation Builder** turns scores and cost breakdowns into short reasons. Every highlighted card should answer *why*.

Examples:

- **Best for you:** “Highest overall score — strong landed price and seller trust; reviews slightly below the top-rated option.”
- **Best landed price:** “Lowest estimated total after FX + shipping + import estimate (list price was not the cheapest).”
- **Passed-over cheaper list:** “Lower sticker price, but border fees and shipping make landed cost higher.”

Explanations cite the factors that were actually decisive, not a black-box rank and not a list of everything the offer happened to score well on. A criterion is stated as a reason only when the offer's **weighted** contribution on it (slider weight × normalized score) exceeds that of the offer it had to outrank, so what the user reads is the margin that produced the result under their own weights.

### 12. Close alternatives (careful)

Alternatives are **not** random similar titles. They are deliberate “you might prefer this instead” candidates in two families:

1. **Same product family, different specs**  
   e.g. double the storage for ~5% more landed cost.
2. **Different but comparable product**  
   e.g. rival model with better value on the user’s weighted criteria.

**Guardrails (important):**

- Never present a different-spec SKU as the same offer; keep exact matches and alternatives separate.
- Alternatives are scored in the **same normalization pass** as the ranked list, then split off. Scoring them separately would put them on their own 0–1 scale and make any comparison against the top pick meaningless.
- Alternatives are ranked by final score and each may carry a **badge** — upgrade, downgrade, or rival — awarded by a value test: a meaningful spec gain for a modest cost increase, a clear saving that still meets the confirmed minimum, or a different product with enough attribute overlap and a score close to the top pick. Thresholds are listed in [parameters.md](parameters.md#alternatives) and explained in [proposed-algorithm.md](proposed-algorithm.md).
- The value tests gate the **badge, not the listing**. A near-offer that clears none is still shown, ranked, with a caveat saying so — the badge is a claim about value and must be earned, but the user is not served by hiding that an option exists.
- Different products need shared category + comparable form factor; require enough attribute overlap; avoid “alternative” drift into unrelated devices.
- Cap alternatives at 3. Fill the slots with one upgrade, one downgrade and one rival where possible before topping up with the highest-scoring unbadged candidates — three near-duplicates say one thing three times.
- Each alternative gets its own explanation: what differs, its landed cost **minus the top pick's** (negative means cheaper), and why it might beat the primary pick for this user.

### 13. Decision Page

The main UI. Show **why**, original price + FX (rate and timestamp), landed-cost add-ons (shipping, tax, duty — mark estimates), commercial terms, spec diffs, and the lenses below. Full layout: [ui-concept.md](ui-concept.md).

**Availability freshness:** every offer card shows a visible `collectedAt` timestamp ("price seen 3 min ago"), and a stale reading is warned about rather than presented as current. Purchasability is established **during the search** — adapters read `stockStatus` from the listing, `out_of_stock` offers never enter the ranking, and `unknown` stock is discounted in confidence. There is deliberately no second re-check when the user clicks through; see [data-source-strategy.md](data-source-strategy.md#why-there-is-no-on-click-re-check).

There are exactly five highlight lenses, and they are the five members of `HighlightKind`. The card label is what the user reads; the kind is what the API returns.

| `HighlightKind` | Card label | Meaning |
| --- | --- | --- |
| `best_overall` | Best for you | Highest final score under the user’s weights. Picked first |
| `lowest_list_price` | Lowest list price | Cheapest sticker after currency conversion, before shipping and border fees |
| `lowest_total_cost` | Lowest total landed cost | Cheapest estimated total. Only offers with a usable cost estimate compete |
| `best_seller` | Most trusted seller | Highest score on the seller criterion (seller record blended with site reputation) |
| `best_warranty` | Best warranty | Longest parsed warranty. Offers with unparseable or absent warranty do not compete |

“Lowest list price” and “lowest total landed cost” are separate lenses on purpose: the gap between them is the whole cross-border argument, and collapsing them would hide it. Reasoning is shown with (or immediately under) each card — not buried.

A lens is skipped rather than filled with a weak answer when nothing qualifies: no offer with a complete landed cost means no “lowest total landed cost” card. And one offer holds at most one highlight — “best for you” is assigned first, so any other lens that would name the same offer is dropped rather than duplicating the card.

There is no “best rated” lens. Review volume is a criterion inside the score, not a lens of its own, because a review count is only meaningful next to the seller it belongs to. There is no “best specification” lens either: every offer in the ranked list matched the confirmed build, so they all share the same specs.

---

## Service boundaries (summary)

- **Frontend** — welcome search, optional sliders, country/currency waterfall, confirm popup, loading, Decision Page.
- **API / Orchestrator** — one search session; skips confirm popup when the catalog match is unique and valid; coordinates the pipeline.
- **Query Normalizer** — parse and clean user text into structured attributes.
- **Reference Catalog** — small validation/normalization reference.
- **Confirmation Gate** — popup on search for model/specs **only when needed**.
- **Source Adapters** — per retailer/API/scraper acquisition.
- **Product Matcher** — merge exact offers; tag near variants separately.
- **FX Service** — live rates → common currency.
- **Landed Cost Layer** — shipping, border tax, registration, other destination fees after FX.
- **Ranking Engine** — weighted scoring on landed cost and quality signals.
- **Explanation Builder** — human-readable why for primary picks.
- **Alternative Scout** — guarded same-spec-variant and cross-product suggestions.
- **Result Formatter** — packages cards + breakdowns + reasons for the UI.

## Data model

The pipeline is modeled as **domain objects**: entities (`ProductVariant`, `Offer`, `SearchSession`) and immutable value objects (`Money`, `FxQuote`, `LandedCost`). Original prices are never overwritten; FX and landed cost are additional objects.

See [data-model.md](data-model.md) for fields, relationships, and what is persisted vs computed per search.

## Storage strategy

Persist only what the system needs to operate: small reference catalog, optional short-lived offer/FX/fee cache, and search-session state (including confirmation choices). Do not mirror the whole web as a price database.

## Core philosophy

**What are they looking for?** → **what matters to them (weights, destination, currency)?** → normalize → **if catalog match is unique and valid, proceed; otherwise confirm** → **which exact product?** → match → **where/how much worldwide, now?** → live fetch → **common currency?** → FX → **true total to get it here?** → landed costs → weight & rank → **why?** → explain → **what else is worth a look?** → careful alternatives → present.

**Main advantage:** current worldwide comparison without a huge static price DB, optimized for *decision support*.  
**Main challenges:** live source volatility, matching accuracy, honest landed-cost estimates, disciplined alternatives, and source access/limits.
