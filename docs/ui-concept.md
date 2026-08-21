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

Stays on the welcome screen. Shown when the query is **invalid**, **incomplete**, or **ambiguous** — not when a single valid catalog variant is already fully specified.

Skip the popup when normalization already matches a **single valid** catalog variant with every required identity property filled. After they finish the popup, continue to loading.

### Two kinds of missing property

Catalog properties fall into two roles (per category):

| Role | Examples | If the user did not specify it |
| --- | --- | --- |
| **Identity / scoring** | storage, RAM, region/version | Popup with **available catalog options only** — they **must** pick one. No “not important.” |
| **Non-scoring / cosmetic** | colour, finish | Popup with available options **plus “Not important”**. If they pick that, search runs across **all** those options. |

Identity properties narrow *which product* we are deciding on and feed matching / spec comparison. Cosmetic properties do not affect the score — so the user may leave them open.

### Incomplete query example

User types: `Samsung S26` (no storage).

Popup:

> Which storage do you want?
> - 256 GB  
> - 512 GB  
> - 1 TB  

They must choose. Search does not start until identity gaps are closed.

If colour is also an available catalog option and was not typed:

> Colour?
> - Black  
> - Silver  
> - **Not important**

- Pick **Black** → search only black variants of the chosen storage.  
- Pick **Not important** → search & selection across **all** colours for that storage/model.

### Invalid value example

Catalog has 512 GB and 1 TB; user typed `600 GB`:

> 600 GB isn’t a valid option. Are you looking for **512 GB** or **1 TB**?

Still no “not important” — storage is identity.

### Multiple prompts in one popup

If several properties are missing (e.g. storage + colour), one popup can ask for all of them before search. Required fields must be answered; optional fields may use **Not important**.

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
- **Freshness timestamp** on every card ("price seen 3 min ago")
- **On-click re-check:** when the user clicks a retailer link, quick-verify availability before redirecting. Warn if the item is gone or the price changed; disclaim if re-check times out
