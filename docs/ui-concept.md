# Initial UI concept

Week 1 screen sketch — not a coded frontend. Three main screens. The **Decision Page** is the assignment centerpiece.

```
Welcome (search)
    → confirm popup only if the product/spec is not real or is ambiguous
    → Loading (while results form)
    → Decision Page
```

---

## 1. Welcome / search

First screen. Enough to start a search without filling a long form.

- **Search bar** — product query
- **Weight sliders** — optional; published defaults if left alone (must still sum to 1)
- **Destination country** and **reference currency** — next to the sliders; also optional
- **Catalogue browse** — optional explore path into the small reference catalog (not required to search)

Country and currency are never required fields. They follow a **waterfall**; each later step overwrites the one before:

| Priority | When | Result |
| --- | --- | --- |
| 1. Default | App load | **TR** + **TRY** |
| 2. Geolocation | User permits location | Inferred country + that country’s usual currency **replaces** the default |
| 3. Manual | User picks country/currency next to the sliders | **Replaces** whatever default or geo had set |

Search uses whatever is in effect at submit. Manual choice is not snapped back to location. Changing sliders later can re-rank without a new crawl; changing destination/currency may require recomputing FX and landed cost.

---

## 2. Confirm popup (not a full page)

Stays on the welcome screen. Shown **only** when they try to search a product that is not a real catalog variant or is ambiguous.

Example: `600 GB` iPhone / `600 GB` on a model that only has 512 GB and 1 TB.

> 600 GB isn’t a valid option. Are you looking for **512 GB** or **1 TB**?

Skip the popup when normalization already matches a **single valid** catalog variant. After they pick, continue to loading.

---

## 3. Loading / wait

Shown while live fetch, FX, landed cost, ranking, and explanations run.

- Fun animation and/or rotating fun facts
- Do **not** add fake delay; overlay real work
- Optional status if a source is slow (“still waiting on UK…”) so it does not feel stuck

---

## 4. Decision Page (most important)

Almost everything for the purchasing decision, in one place:

- Recommended offer(s) and **why** (plain-language explanation)
- Original price + original currency (never overwritten)
- FX rate + conversion timestamp + amount in the reference currency
- Landed-cost add-ons: shipping, border/import tax, registration and similar fees (mark **estimated** vs quoted)
- Commercial: seller, reliability, stock, delivery, warranty, returns
- Specs and differences vs the confirmed variant
- Lenses: lowest list price, lowest total landed cost, best for you (weights), plus other “best of” where they apply
- Close alternatives (same family different specs, or a comparable product) with their own why — or none if nothing passes the guardrails
