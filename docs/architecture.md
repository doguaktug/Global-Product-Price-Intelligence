# Product Price Intelligence — System Architecture

Personal project draft. Full decision-support model (not only a ranking engine).

**Core idea:** The user enters a product; the system understands and normalizes the query, **confirms ambiguous model/specs with the user**, then pulls live worldwide offers. Prices are converted with current FX, **landed costs** (shipping, border tax, registration fees) are added, offers are matched, ranked by user preferences, and presented with **explicit reasoning** — including carefully selected close alternatives.

## High-level components

| Component | Responsibility |
| --- | --- |
| Frontend | Search, preference weights, confirmation UI, result cards, explanations, alternatives |
| API / Orchestrator | Owns one search session; coordinates services and human-in-the-loop confirmation |
| Query Normalizer | Typos, category, brand, model, capacity and other attributes |
| Reference Catalog | Small reference data for normalization / validation (not a price warehouse) |
| Confirmation Gate | Resolves ambiguous or invalid specs with the user before live search |
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
2. Normalize (+ catalog validation)
3. Confirm model/specs with user   ← gate before live work
4. Live worldwide acquisition
5. Product matching (same vs near)
6. FX conversion
7. Landed cost (shipping, tax, fees)
8. Ranking by user weights
9. Build explanations / reasoning
10. Scout close alternatives (careful rules)
11. Present results
```

### 1–2. Understand and normalize

The user does not need a perfect product name (`Aple`, wrong capacity, etc.). The normalizer extracts category, brand, model, and technical attributes; fixes typos; and checks against valid catalog options.

Colors and other non-core, frequently changing fields need not live in the catalog. The catalog answers “what product could this be?” Live data answers “where, how much, under what conditions — now?”

### 3. Confirm model / specs with the user (gate)

**Do not silently invent variants.** If the input is invalid or ambiguous, stop and ask before starting live search.

Example: catalog has 512 GB and 1 TB, user typed “600 GB”:

> 600 GB isn’t a valid option for this model. Are you looking for **512 GB** or **1 TB** (or another nearby configuration)?

Also confirm when:

- brand/model is fuzzy and multiple catalog candidates remain
- required comparison attributes are missing (e.g. storage or RAM for phones/laptops)
- the user may mean a model family rather than a specific SKU

Only after confirmation does the orchestrator kick off live acquisition. This saves cost/latency and prevents ranking nonsense.

### 4. Live data acquisition

Three approaches: official API (preferred), HTML scraping, headless browser for JS-rendered pages. Worldwide sources may return prices in different currencies and under different commercial terms.

### 5. Product matching

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

### 6. FX conversion

Convert offer list prices into a common currency via a live exchange-rate provider (no custom FX engine). Example: USD/EUR offers → TRY (or user’s preferred currency) using current rates, then pass amounts into landed-cost and ranking on the same scale.

### 7. Landed cost (after FX)

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

### 8. Ranking with user preferences

Goal is not only cheapest list price — it is the best fit for this user. Collect weights up front (or defaults), e.g.:

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

Lower landed cost → higher PriceScore. Uncertain landed-cost offers should carry a confidence penalty.

### 9. Explanation / reasoning (before presentation)

Before the UI shows winners, an **Explanation Builder** turns scores and cost breakdowns into short reasons. Every highlighted card should answer *why*.

Examples:

- **Best for you:** “Highest overall score — strong landed price and seller trust; reviews slightly below the top-rated option.”
- **Best landed price:** “Lowest estimated total after FX + shipping + import estimate (list price was not the cheapest).”
- **Passed-over cheaper list:** “Lower sticker price, but border fees and shipping make landed cost higher.”

Explanations should cite the decisive factors (weights, landed-cost components, confidence), not a black-box rank.

### 10. Close alternatives (careful)

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

### 11. Result presentation

| Card | Meaning |
| --- | --- |
| Best landed price | Lowest estimated total cost in the common currency (FX + fees). |
| Best rated / trust | Strong on reviews and related quality signals. |
| Best for you | Highest final score under the user’s weights. |
| Close alternatives | Up to ~3: same model different specs and/or comparable products, each with rationale. |

“Cheapest sticker” and “best for you” stay distinct. Reasoning is shown with (or immediately under) each card — not buried.

---

## Service boundaries (summary)

- **Frontend** — search, preference weights, confirmation prompts, explained result cards, alternatives.
- **API / Orchestrator** — one search session; enforces confirm-before-fetch; coordinates the pipeline.
- **Query Normalizer** — parse and clean user text into structured attributes.
- **Reference Catalog** — small validation/normalization reference.
- **Confirmation Gate** — human check on model/specs when needed.
- **Source Adapters** — per retailer/API/scraper acquisition.
- **Product Matcher** — merge exact offers; tag near variants separately.
- **FX Service** — live rates → common currency.
- **Landed Cost Layer** — shipping, border tax, registration, other destination fees after FX.
- **Ranking Engine** — weighted scoring on landed cost and quality signals.
- **Explanation Builder** — human-readable why for primary picks.
- **Alternative Scout** — guarded same-spec-variant and cross-product suggestions.
- **Result Formatter** — packages cards + breakdowns + reasons for the UI.

## Storage strategy

Persist only what the system needs to operate: small reference catalog, optional short-lived offer/FX/fee cache, and search-session state (including confirmation choices). Do not mirror the whole web as a price database.

## Core philosophy

**What are they looking for?** → normalize → **confirm the real model/specs** → **which exact product?** → match → **where/how much worldwide, now?** → live fetch → **common currency?** → FX → **true total to get it here?** → landed costs → **what matters to this user?** → weight & rank → **why?** → explain → **what else is worth a look?** → careful alternatives → present.

**Main advantage:** current worldwide comparison without a huge static price DB, optimized for *decision support*.  
**Main challenges:** live source volatility, matching accuracy, honest landed-cost estimates, disciplined alternatives, and source access/limits.
