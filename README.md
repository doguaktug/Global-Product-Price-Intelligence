# Global Product Price Intelligence

Prototype decision-support app: research a product across countries and sources, normalize offers, compare **total landed cost** and specs, and recommend the best choice **with an explanation**.

Not a conventional price-comparison site. The core question:

> Given the available global options, which product/offer is the best choice for the user, and why?

## Stack

- **Language:** Python 3.11+
- **API:** FastAPI
- **Domain models:** Pydantic v2
- **Layout:** `src/gp_price_intel/` package mirrored to the approved architecture

## Quick start

Python 3.11+. Outbound HTTPS is required (live FX via Frankfurter). eBay keys are optional.

```bash
git clone https://github.com/doguaktug/Global-Product-Price-Intelligence.git
cd Global-Product-Price-Intelligence

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # empty eBay keys are fine
pytest
uvicorn gp_price_intel.api.main:app --reload --app-dir src --reload-dir src
```

Windows (PowerShell), from the repo folder that `git status` shows as `main`:

```powershell
cd path\to\Global-Product-Price-Intelligence
git checkout main
git pull
# A venv copied or created without pip cannot run `python -m pip`.
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Port 8000 is often in Windows' excluded range (Hyper-V / WSL). WinError 10013
# is that block, not a missing package. Use 8080 unless you have freed 8000.
.\.venv\Scripts\python.exe -m uvicorn gp_price_intel.api.main:app --reload --app-dir src --reload-dir src --host 127.0.0.1 --port 8080
```

If `ensurepip` itself is missing, recreate the venv from a Python that has pip:

```powershell
py -3.12 -m venv --clear .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Stop any older uvicorn first (Ctrl+C in that terminal). `WinError 10013` means Windows refused the bind (excluded port or reserved by HTTP.sys), not that the app crashed. Confirm with `netsh interface ipv4 show excludedportrange protocol=tcp` — if 8000 sits in a range, keep `--port 8080`. If another process already owns the port, the new process will not replace it and the browser keeps the previous UI. Hard-refresh the page (Ctrl+F5). `/health` should show `"used_filter": true` and `web_dir` ending in `src\gp_price_intel\web`, not `site-packages`.

| URL | What it is |
| --- | --- |
| `http://127.0.0.1:8000/` | **The product UI** — search → optional confirm → loading → Decision Page |
| `http://127.0.0.1:8080/` | Same UI when Windows refuses port 8000 (`--port 8080`) |
| `http://127.0.0.1:8000/docs` | Swagger for the JSON API (not the comparison screens) |
| `http://127.0.0.1:8000/health` | Process is up |

Listings you see are mostly **fixtures** (`data/fixtures/offers.json`) running through the real pipeline. Live eBay offers appear only if `EBAY_APP_ID` and `EBAY_CERT_ID` are set in `.env`.

**Searches that exercise the UI**

| Query | What happens |
| --- | --- |
| `MacBook Air M4 512GB 16GB RAM Sky Blue` | Unique catalog hit — goes straight to loading, then the Decision Page. Scroll down for alternatives |
| `Samsung Galaxy S26 Ultra 512 GB Black` | Same path, phone fixtures |
| `Apple iPhone 16 Plus 256 GB White` | Previously eBay-only — now has fixture offers too |
| `ASUS Zenbook 14 OLED 512GB 16GB Intel Core Ultra 7 Foggy Silver` | Same for a laptop family that had no fixtures |
| `Samsung S26` | Confirm popup: which model (S26 / Plus / Ultra), then storage / colour |
| `samsung s25` | Confirm popup: S25 / Plus / Ultra, then that model's specs |
| `asus` | Confirm popup: ROG / Zenbook / Vivobook / TUF, then the line/model for that series |
| `asus rog` | Confirm popup: Strix / Zephyrus / Flow, then G14 / G16 (etc.), then that laptop's specs |
| `Samsung S26 Ultra 600 GB` | Confirm popup (600 GB is not a real option) |

Leave the sliders and country/currency at defaults (TR + TRY) unless you want to change how offers are scored. Destination and currency are applied when the search runs (FX and landed cost). Used, refurbished, and open-box listings are excluded unless you tick **include used / refurbished** next to those buttons.

### Checking the live eBay adapter

Offers come from `data/fixtures/offers.json` plus eBay, and eBay is only searched when `EBAY_APP_ID` and `EBAY_CERT_ID` are set in `.env`. A Decision Page cannot tell you *why* a source contributed nothing, so ask the adapter directly:

```bash
python -m gp_price_intel.diagnose "Samsung Galaxy S26 512GB"
```

It prints which `.env` was loaded, whether the keys are set (never their values), which eBay host it talked to, the exact keyword query sent to Browse, and then where the listings stop — credentials rejected, nothing returned, returned but discarded, or returned but unmatched against the catalog.

Keep **`EBAY_SANDBOX=false`**. The sandbox is a separate eBay with its own keyset and virtually no inventory, so it authenticates fine and returns zero listings, which looks exactly like a product nobody sells.

Every catalog variant has at least two **new** fixture offers (DE MediaMarkt + TR Electronics, plus the original curated rows). A Decision Page still populates when live eBay is unreachable or unconfigured.

## Package map

| Path | Role |
| --- | --- |
| `src/gp_price_intel/domain/` | Entities + value objects (`Money`, `Offer`, `SearchSession`, …) |
| `src/gp_price_intel/catalog/` | Reference catalog loader |
| `src/gp_price_intel/normalize/` | Query normalizer + confirmation prompts |
| `src/gp_price_intel/adapters/` | Per-source adapters (API / HTML / fixture) |
| `src/gp_price_intel/fx/` | Exchange-rate conversion (preserves original price) |
| `src/gp_price_intel/landed_cost/` | Shipping / tax / duty estimates |
| `src/gp_price_intel/matching/` | Identical / similar / different matching |
| `src/gp_price_intel/ranking/` | Weighted decision algorithm |
| `src/gp_price_intel/explanation/` | Plain-language why |
| `src/gp_price_intel/alternatives/` | Guarded close alternatives |
| `src/gp_price_intel/orchestrator/` | End-to-end search session |
| `src/gp_price_intel/api/` | FastAPI routes |
| `src/gp_price_intel/web/` | Search, loading, Decision Page + alternatives |
| `data/catalog/` | Seed categories / families / variants |
| `data/sources/` | Source registry |
| `data/fixtures/` | Demo offer snapshots |

The pipeline runs end-to-end on the API: normalize → confirm → fetch → match → FX → landed cost → rank → explain → Decision Page payload. FastAPI also serves the comparison UI at `/` (search → optional confirm popup → loading → Decision Page, with alternatives further down the same page).

**Process**

Chart and STEP writeup: [architecture.md — End-to-end process flow](docs/architecture.md#end-to-end-process-flow) · [process-framework-and-algorithm.md](docs/process-framework-and-algorithm.md). Below, each step lists the modules/methods that realize it today.

1. **User enters a product**  
   `api/routes.py` → `start_search` · `orchestrator/search.py` → `SearchOrchestrator.start_session`

2. **User selects preference weights** (optional sliders; country/currency: TR+TRY → geo if permitted → manual; later overwrites earlier)  
   `web/` search screen · `domain/models.py` → `UserPreferences` (defaults) · wired through `start_search` / `StartSearchRequest.preferences`

3. **Normalize the query against a small reference catalog**  
   `normalize/query_normalizer.py` → `QueryNormalizer.normalize` · `normalize/similarity.py` → `score_query_against_labels`, `similarity`, `strip_spec_tokens` · `normalize/attribute_parser.py` → `parse_storage_gb`, `parse_memory_gb`, `parse_region_version`, `parse_colour` · `catalog/repository.py` → `CatalogRepository` (`get_family`, `list_variants`, …) · preview: `api/routes.py` → `normalize_query` / `SearchOrchestrator.preview_normalization`

4. **Confirm popup only if needed** — skip when fully specified; missing identity props (e.g. storage on `Samsung S26`) require a choice; optional props (e.g. colour) may be **Not important**  
   `web/` confirm popup · `normalize/confirmation.py` → `resolve_search_scope`, `filter_variants` · `orchestrator/search.py` → `SearchOrchestrator.apply_choices` · `api/routes.py` → `confirm_search`

5. **Loading** (animation / fun facts over real work)  
   `web/` loading view over `api/routes.py` → `run_search` → `SearchOrchestrator.run`

6. **Acquire live worldwide offers**  
   `adapters/registry.py` → `build_adapters`, `load_sources` · `adapters/ebay.py` → `EbayAdapter.search` · `adapters/fixture.py` → `FixtureAdapter.search` · `adapters/base.py` → `SourceAdapter` · orchestrated by `SearchOrchestrator._fetch_offers`

7. **Match exact products** (keep different specs separate)  
   `matching/matcher.py` → `ProductMatcher.match` (`_match_by_identifiers`, `_match_by_attributes`) · `matching/identifiers.py` → `extract_offer_identifiers`, `gtin_matches`, `code_matches`

8. **Convert with live FX**, then add **shipping, border tax, registration and similar fees**  
   `fx/service.py` → `FxService.convert` · `landed_cost/service.py` → `LandedCostService.estimate` · called from `SearchOrchestrator.run`

9. **Rank** by the user’s weights on landed cost and quality signals  
   `ranking/engine.py` → `RankingEngine.score` · `ranking/confidence.py` → `compute_data_confidence`, `is_highlight_eligible`, `reliability_warning` · `ranking/highlights.py` → `pick_highlights` (confidence floor 0.7)

10. **Build reasoning** for each highlighted choice  
    `explanation/builder.py` → `ExplanationBuilder.build` · attached in `SearchOrchestrator.run` onto `ScoreBreakdown.explanation` / highlight cards

11. **Suggest close alternatives** carefully (same product different specs, or a comparable different product)  
    `alternatives/scout.py` → `AlternativeScout.select`

12. **Present the Decision Page** (why, FX, landed-cost add-ons, the five highlight lenses + alternatives)  
    `domain/models.py` → `DecisionPage` (`offers`, `offer_scores`, `highlights`, `alternatives`, `alternative_offers`) · returned by `run_search` / `SearchOrchestrator.run` · rendered by `web/` (overlapping highlight lenses collapse into 1–5 cards)

## Design docs

- [System architecture](docs/architecture.md) — process, services, ranking and alternatives
- [Data model](docs/data-model.md) — domain objects (entities + value objects), not a price warehouse
- [Data source strategy](docs/data-source-strategy.md) — catalog vs live adapters vs FX vs fee rules
- [Initial UI concept](docs/ui-concept.md) — welcome, confirm popup, loading, Decision Page
- [Proposed algorithm](docs/proposed-algorithm.md) — weighted scoring, missing-data handling, explanations, alternative guardrails
- [Process framework](docs/process-framework-and-algorithm.md) — STEP flow from first page to Decision Page
- [Parameter reference](docs/parameters.md) — every tunable value, where it is defined, and what moving it does
- [Project layout](docs/project-layout.md) — Python package map and current backend status
