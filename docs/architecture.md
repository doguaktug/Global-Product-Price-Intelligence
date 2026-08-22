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
| Product Matching | Catalog + attributes + text similarity; same product vs near variant |
| FX Service | Live exchange rates into a common currency |
| Landed Cost Layer | Shipping, border/import tax, registration and similar destination fees |
| Ranking Engine | User weights + total cost + trust + reviews (+ delivery signals) |
| Explanation Builder | Why each highlighted choice won (or lost) before presentation |
| Alternative Scout | Same-product different specs, or different but comparable products |
| Result Presentation | Best price, best for you, best rated, and close alternatives — each with rationale |

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

Screens: **Welcome → (confirm popup) → Loading → Decision Page.** See [ui-concept.md](ui-concept.md). Assignment-style writeup of the same process and the ranking algorithm: [process-framework-and-algorithm.md](process-framework-and-algorithm.md).

### 1. User query

The user types what they want to buy (need not be a perfect product name).

### 2. User selects preference weights

This is a first-class user step, not a hidden ranking default. On the **welcome / search** screen, next to the search bar:

- **Criterion weights** (sliders) that must sum to 1, e.g. landed price, seller trust, warranty, specs, reviews, delivery
- **Destination country** and **reference currency** — optional controls next to the sliders
- Optional **catalogue browse** if they want to explore instead of typing

If they do not move the sliders, **published defaults** apply (e.g. price 50%, seller 25%, reviews 15%, delivery 10%) and the session still records `UserPreferences` — ranking never invents weights after the fact. The user can change weights later and re-rank without re-fetching offers.

**Country / currency waterfall** (each later step overwrites the one before):

1. **Default:** TR + TRY  
2. **If they permit geolocation:** inferred country + that country’s usual currency replaces the default  
3. **If they manually select** next to the sliders: that replaces default or geo  

Search uses whatever is in effect at submit. Manual choice is not snapped back to location.

### 3. Understand and normalize

The user does not need a perfect product name (`Aple`, wrong capacity, etc.). The normalizer extracts category, brand, model, and technical attributes; fixes typos; and checks against valid catalog options.

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

| Approach | Logic | Strong when |
| --- | --- | --- |
| Identity-based | EAN/UPC/GTIN, model code, SKU | Strong IDs exist across listings |
| Attribute-based | Brand, model, RAM, storage, screen, etc. | Phones / laptops / tablets |
| Text similarity | Title/description similarity | Missing model codes / messy titles |
| Hybrid | Identity → attributes → text | Default overall strategy |

Matching must distinguish:

- **Exact same product** (same model + same critical specs)
- **Same model, different specs** (e.g. 256 GB vs 512 GB) — candidate for “close alternative,” not a merged offer
- **Different but comparable product** — also alternative territory, with stricter rules

### 8. FX conversion

Convert offer list prices into a common currency via a live exchange-rate provider (no custom FX engine). Example: USD/EUR offers → TRY (or user’s preferred currency) using current rates, then pass amounts into landed-cost and ranking on the same scale.

### 9. Landed cost (after FX)

For **worldwide** options, list price in common currency is not enough. After FX, compute an estimated **total landed cost** toward the user’s destination:

- shipping / delivery to destination
- border / import / VAT / duty estimates where applicable
- registration or other mandatory destination fees when relevant to the category/region

```
LandedCost ≈ FX(ListPrice) + Shipping + BorderTaxEstimate + RegistrationFees + OtherKnownFees
```

Rules of thumb for the prototype:

- Prefer source-provided shipping when available; otherwise use a transparent estimate and label it as estimated.
- Keep fee breakdowns visible in explanations (so “cheaper list price, higher landed cost” is understandable).
- If a fee cannot be estimated reliably, mark the offer’s total cost as **partial / uncertain** and down-rank or flag confidence — do not pretend precision.

Ranking and “best price” should prefer **landed cost**, not raw list price, when comparing across countries.

### 10. Ranking with user preferences

Goal is not only cheapest list price — it is the best fit for this user. Use the **weights already chosen in step 2** (including defaults if the user left them unchanged), e.g.:

- price (landed) 50%
- seller trust 25%
- review score 15%
- delivery 10%

Normalize each criterion to a comparable scale, then:

```
FinalScore = w_price × PriceScore
           + w_seller × SellerScore
           + w_review × ReviewScore
           + w_delivery × DeliveryScore
```

Lower landed cost → higher PriceScore. Uncertain landed-cost offers carry a confidence penalty. Missing criteria are excluded per offer with weight re-normalization.

Full algorithm: [proposed-algorithm.md](proposed-algorithm.md) — normalization, missing-data rules, confidence, highlight selection, alternative guardrails, and explanation generation.

### 11. Explanation / reasoning (before presentation)

Before the UI shows winners, an **Explanation Builder** turns scores and cost breakdowns into short reasons. Every highlighted card should answer *why*.

Examples:

- **Best for you:** “Highest overall score — strong landed price and seller trust; reviews slightly below the top-rated option.”
- **Best landed price:** “Lowest estimated total after FX + shipping + import estimate (list price was not the cheapest).”
- **Passed-over cheaper list:** “Lower sticker price, but border fees and shipping make landed cost higher.”

Explanations should cite the decisive factors (weights, landed-cost components, confidence), not a black-box rank.

### 12. Close alternatives (careful)

Alternatives are **not** random similar titles. They are deliberate “you might prefer this instead” candidates in two families:

1. **Same product family, different specs**  
   e.g. double the storage for ~5% more landed cost.
2. **Different but comparable product**  
   e.g. rival model with better value on the user’s weighted criteria.

**Guardrails (important):**

- Never present a different-spec SKU as the same offer; keep exact matches and alternatives separate.
- Spec upgrades should clear a **value test**, e.g. meaningful capacity/RAM/CPU gain vs modest landed-cost delta — threshold configurable (illustrative: large storage jump for ≤ ~5–10% cost increase).
- Spec downgrades only if they save clearly and still meet confirmed minimum requirements from the confirmation gate.
- Different products need shared category + comparable form factor; require enough attribute overlap; avoid “alternative” drift into unrelated devices.
- Cap alternatives (e.g. ~3). Prefer diversity of *reason* (better value upgrade, cheaper acceptable downgrade, strong rival) over three near-duplicates.
- Each alternative gets its own explanation: what differs, cost delta, and why it might beat the primary pick for this user.
- If no candidate passes the guardrails, show fewer alternatives (or none) rather than weak suggestions.

### 13. Decision Page

The main UI. Show **why**, original price + FX (rate and timestamp), landed-cost add-ons (shipping, tax, duty — mark estimates), commercial terms, spec diffs, and the lenses below. Full layout: [ui-concept.md](ui-concept.md).

**Availability freshness:** every offer card shows a visible `collectedAt` timestamp ("price seen 3 min ago"). When the user clicks a retailer link, the system performs a **quick re-check** of that listing (lightweight re-fetch of stock/price) before redirecting. If the item is no longer available or the price has changed materially, show a warning instead of silently forwarding to a dead page. If re-check fails or times out, redirect anyway with a disclaimer: "We couldn't verify — confirm on the retailer's page."

| Card | Meaning |
| --- | --- |
| Best landed price | Lowest estimated total cost in the common currency (FX + fees). |
| Best rated / trust | Strong on reviews and related quality signals. |
| Best for you | Highest final score under the user’s weights. |
| Close alternatives | Up to ~3: same model different specs and/or comparable products, each with rationale. |

“Cheapest sticker” and “best for you” stay distinct. Reasoning is shown with (or immediately under) each card — not buried.

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
