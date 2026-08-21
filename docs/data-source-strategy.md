# Data source strategy

How the system **obtains** data — not how it stores it. Catalog, FX, offers, and fees come from different places on purpose.

Assignment constraints this has to satisfy: research **4–5 countries** and **2–3 categories**; use a **meaningful mix of source types**; prefer APIs; respect ToS / robots.txt / rate limits; **do not** design around uncontrolled scraping. Goal is `Search → Identify → Normalize → Compare → Evaluate → Recommend`, not “cover the internet.”

---

## Principles

1. **API first.** If a source publishes a usable product/offer API, use it.
2. **One adapter per source.** The orchestrator talks to a common `SourceAdapter` interface; Amazon-DE and MediaMarkt-TR never share parsers.
3. **Do not abuse scrape.** No login walls, CAPTCHA bypass, high-rate crawls, or ignoring robots.txt. If a source cannot be fetched cleanly, skip it or use a **recorded fixture** for the demo.
4. **Preserve provenance.** Every `Offer` knows `sourceId`, `listingUrl`, `collectedAt`. Reliability and freshness travel with the row.
5. **Separate feeds.** Product identity comes from **our catalog**; prices/commercial terms come from **live adapters**; FX from a **dedicated FX provider**; landed-cost gaps from **explicit rules**, labeled as estimates.
6. **Demo must not depend on a flaky crawl.** Each adapter can return live data **or** a pinned fixture of the same shape so the Decision Page still works.

---

## Four feeds (not one crawl)

| Feed | Purpose | Typical origin | Persistence |
| --- | --- | --- | --- |
| **Reference catalog** | Normalize / validate / confirm | Curated by us (seed JSON) | Persisted, small |
| **Offer adapters** | Live price, seller, stock, shipping hints, raw specs | Retailer API or permitted page | Per search (+ short cache) |
| **FX provider** | Rate + timestamp into reference currency | Public FX API | Short cache |
| **Landed-cost rules** | Shipping/tax/duty/registration when the listing does not quote them | Our rules + user destination | Rules persisted; amounts computed |

The catalog is **not** filled from scrape. Live adapters never overwrite `Money` originals.

---

## Acquisition methods (priority order)

```
1. Official / partner REST API
2. Public structured data (JSON:API, schema.org Product / Offer)
3. HTML parse of a public product page — only if ToS + robots allow
4. Headless browser — last resort, same legal bar, JS-only pages
5. Fixture / recorded snapshot — prototype & demo fallback
```

Each adapter implements the same contract:

```
search(searchScope, destination) → list of raw listings
normalize(raw) → Offer (original Money, raw specs, seller, country)
```

`searchScope` includes fixed identity constraints and any optional keys marked **Not important** (all colours, etc.).

If a method is blocked (403, robots disallow, missing API key), the adapter fails **soft**: zero offers from that source, logged, other sources continue.

---

## MVP scope

### Categories (3)

Smartphones, laptops, tablets — matches the architecture and assignment examples.

### Countries (5)

Chosen for currency spread and import-cost contrast, from the assignment list:

| Country | Currency | Why it is in the cut |
| --- | --- | --- |
| Türkiye | TRY | User-home / destination default; local marketplaces |
| Germany | EUR | EU retailer + VAT / intra-EU vs import contrast |
| United States | USD | Official retailer API candidate; US vs EU version |
| United Kingdom | GBP | Post-Brexit fees vs EU |
| Japan | JPY | Region/version + large FX move vs TRY |

UAE / France / Italy can be added later as extra adapters; they are not required for MVP.

### Source *types* to demonstrate (not a huge site list)

| Kind | Role in the decision | Reliability bias |
| --- | --- | --- |
| Manufacturer official store | Specs + list price, strong warranty signal | Highest |
| Authorized / major retailer | Stock, shipping quotes, return policy | High |
| Large e-commerce | Coverage, competitive price | Medium–high |
| Marketplace (3rd-party sellers) | Often cheapest sticker; weaker seller/warranty | Lowest — still useful if flagged |

A **small** set that hits these types across the five countries is enough. Count of URLs is not the grade.

---

## Proposed MVP adapters

Concrete starting set. Keys/ToS must be checked before any live call; if a row cannot be used legally, drop it and keep the interface.

| Adapter | Country | Kind | Method (preferred) | What we take |
| --- | --- | --- | --- | --- |
| Official brand store (e.g. Apple or Samsung regional) | US / DE / TR if a public page or API exists | Manufacturer | API or structured page | List price, SKU, warranty, specs |
| Best Buy Products API | US | Major retailer | Official REST API | Price, availability, specs, image |
| A TR major retailer or marketplace (e.g. authorized electronics chain / Hepsiburada-class) | TR | E-commerce | API if any; else permitted page **or fixture** | Local TRY offer |
| A DE electronics retailer (MediaMarkt/Saturn-class or Amazon.de *only* if an official API/partner path exists) | DE | E-commerce | Same rule | EUR offer |
| A UK retailer (Currys-class) | UK | Authorized / e-commerce | Same rule | GBP offer |
| A JP retailer or marketplace | JP | E-commerce / marketplace | Same rule | JPY offer |
| eBay Browse API (optional) | Multi | Marketplace | Official API | 3rd-party price; **low seller reliability** |

Amazon storefront scraping is **out** unless an official product advertising / partner API is available and licensed. Same for Trendyol/Hepsiburada if ToS forbids bots: use fixture or skip.

**Week 2 practical cut:** ship **≥2 live adapters** (FX + Best Buy or eBay + one more) and **fixtures** for the remaining countries so the five-country Decision Page still has offers. Replace fixtures with live adapters as keys/ToS allow.

---

## FX strategy

Currency is a **separate source**, not scraped from retailer pages.

| Need | Approach |
| --- | --- |
| Rate + timestamp | Call a dedicated FX API per search (or cache ~1 hour) |
| Original price | Never taken from FX; stays on `Offer.listPrice` |
| Display | Original + rate + `asOf` + reference amount (`ConvertedMoney`) |

**Provider preference:**

1. **Frankfurter** (ECB daily rates, no API key) — good default for the prototype
2. **Open Exchange Rates** / **ExchangeRate-API** — if we need more currencies or a key is already available
3. Avoid scraping bank websites

ECB-style daily rates are enough for a decision prototype; we still **show date/time**. If the provider is daily, `asOf` is the ECB publication time, not “now to the second.”

---

## Landed-cost data strategy

Do **not** invent a customs microservice. Compose:

| Line | Where it comes from |
| --- | --- |
| List price | Offer adapter (original currency) |
| FX | FX provider |
| Shipping | Listing if quoted; else a **destination rule** (flat/estimated), marked `estimated` |
| VAT / sales tax | Rule table by destination + offer country (e.g. TR import VAT; EU VAT already in many EU list prices — do not double-count) |
| Import duty / ÖTV-like fees | Category + destination **estimate table**, `estimated` or `unavailable` |
| Registration | Only if the category/destination has a known mandatory fee; else omit |

If shipping or duty cannot be estimated honestly, set `LandedCost.completeness = partial | unknown` and say so in the explanation. Ranking prefers complete landed costs over fake precision.

---

## Catalog data strategy

- **Hand-curated seed** for the demo products (assignment: ≥3 products, one with real cross-country differences).
- Fields: family, aliases, valid storage/RAM/region options, canonical specs.
- Optional later: enrich specs from manufacturer pages **once**, not on every search.
- Not a crawled product graph.

This is also what lets confirmation stay quiet: if the user types a **fully specified** variant that exists in this seed, search starts immediately. Incomplete identity fields (e.g. `Samsung S26` without storage) open the popup; optional fields like colour may be left as **Not important**.

---

## Reliability, freshness, missing data

| Signal | Source |
| --- | --- |
| Source reliability | `Source.kind` + static score (manufacturer > authorized > marketplace) |
| Seller reliability | Adapter field when present; else inherit source; marketplace 3rd party stays lower |
| Freshness | `Offer.collectedAt`; optional TTL cache (e.g. 15–30 min) |
| Missing fields | Leave null; `dataConfidence` down; `ScoreBreakdown.missingCriteria` |
| Conflicts | Same variant, different spec strings → keep both `rawText`, prefer manufacturer/catalog canonical spec for comparison |

Adapters must not invent stock, warranty, or shipping to look complete.

---

## Availability and freshness policy

**Problem:** the user clicks "best deal" and the retailer says "item not available." This kills trust.

**Three layers to minimize it:**

### 1. At fetch time — filter honestly

- Adapters must capture `stockStatus` from the listing (not guess from "page exists").
- `out_of_stock` offers are **excluded from ranking** entirely — they are not offers.
- `unknown` stock is allowed but carries a lower `dataConfidence` and an explanation caveat.
- `limited` stock is included with a visible warning on the card.

### 2. Cache TTL — don't serve stale offers

- Offer cache TTL: **15–30 minutes** max.
- After expiry, the next search re-fetches live.
- The Decision Page shows `collectedAt` visibly on every card (e.g. "price seen 3 min ago").

### 3. On click — re-check before redirect

When the user clicks a retailer link on the Decision Page:

1. **Quick re-check:** lightweight re-fetch of stock/price for that one listing (adapter's `check_availability` method — not a full search).
2. **If still available and price is close:** redirect to the retailer.
3. **If gone or price changed materially:** show a warning popup *before* redirecting. Example: "This item appears to be unavailable now" or "Price has changed from €1,399 to €1,499 — continue?"
4. **If re-check fails or times out (e.g. >3s):** redirect anyway with a disclaimer: "We couldn't verify availability — please confirm on the retailer's page."

### What we cannot prevent

A listing can go stale between re-check and the user's actual purchase. We do not control the retailer. The system's job is to **minimize** dead-link clicks and **never pretend** an offer is guaranteed.

---

## Rate limits, caching, failure

- Per-adapter rate limit and timeout.
- Short-lived cache keyed by `(sourceId, variantId)` so repeat searches in a demo do not hammer APIs.
- Parallel fetch with a deadline; slow sources drop out rather than blocking the Decision Page.
- Partial success is success: 3 of 6 sources is a valid page, with a caveat (“UK source unavailable”).

---

## What we will not do

- Scrape behind logins, CAPTCHAs, or “no bots” ToS
- Treat Google Shopping HTML as a free API
- Store a historical price warehouse
- Merge US/EU versions because titles look similar
- Pretend marketplace “Buy Box” shipping is official manufacturer shipping

---

## Adapter contract (maps to the data model)

Each successful listing should fill, as far as the source allows:

- `listingTitle`, `listingUrl`, `imageUrl`
- `listPrice` (`Money` original)
- `country`, `seller`
- `stockStatus`, `deliveryTime`, `warranty`, `returnPolicy` when visible
- `rawSpecs` (`NormalizedSpec` with `rawText`)
- `collectedAt`

FX, landed cost, `matchKind`, and scores are **not** the adapter’s job.

---

## Why this is enough for the assignment

- Multiple **countries** and **source kinds**, without a giant crawl.
- **Currency** from a real FX provider with visible rate + time.
- **Decision quality** still works when some commercial fields are missing (explicit incompleteness).
- Legal/ethical story is defensible in the writeup.
- Week 2 can go end-to-end with two live adapters + fixtures; Week 3 deepens ranking, not site count.
