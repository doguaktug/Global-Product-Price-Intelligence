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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn gp_price_intel.api.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

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
| `data/catalog/` | Seed categories / families / variants |
| `data/sources/` | Source registry |
| `data/fixtures/` | Demo offer snapshots |

Most pipeline modules are implemented end-to-end on the API (normalize → fetch → match → FX → landed cost → rank → Decision Page payload). **Next:** basic comparison UI. Catalog seed data and FastAPI routes load today.

**Process**

Chart and STEP writeup: [architecture.md — End-to-end process flow](docs/architecture.md#end-to-end-process-flow) · [process-framework-and-algorithm.md](docs/process-framework-and-algorithm.md). Below, each step lists the modules/methods that realize it today (UI steps are not coded yet).

1. **User enters a product**  
   `api/routes.py` → `start_search` · `orchestrator/search.py` → `SearchOrchestrator.start_session`

2. **User selects preference weights** (optional sliders; country/currency: TR+TRY → geo if permitted → manual; later overwrites earlier)  
   `domain/models.py` → `UserPreferences` (defaults) · wired through `start_search` / `StartSearchRequest.preferences` · *UI sliders / geo waterfall: not built yet*

3. **Normalize the query against a small reference catalog**  
   `normalize/query_normalizer.py` → `QueryNormalizer.normalize` · `normalize/similarity.py` → `score_query_against_labels`, `similarity`, `strip_spec_tokens` · `normalize/attribute_parser.py` → `parse_storage_gb`, `parse_memory_gb`, `parse_region_version`, `parse_colour` · `catalog/repository.py` → `CatalogRepository` (`get_family`, `list_variants`, …) · preview: `api/routes.py` → `normalize_query` / `SearchOrchestrator.preview_normalization`

4. **Confirm popup only if needed** — skip when fully specified; missing identity props (e.g. storage on `Samsung S26`) require a choice; optional props (e.g. colour) may be **Not important**  
   `normalize/confirmation.py` → `resolve_search_scope`, `filter_variants` · `orchestrator/search.py` → `SearchOrchestrator.apply_choices` · `api/routes.py` → `confirm_search` · *popup UI: not built yet*

5. **Loading** (animation / fun facts over real work)  
   Work starts in `api/routes.py` → `run_search` → `SearchOrchestrator.run` · *loading screen UI: not built yet*

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

12. **Present the Decision Page** (why, FX, landed-cost add-ons, best landed / best for you / best rated + alternatives)  
    `domain/models.py` → `DecisionPage` (`offers`, `offer_scores`, `highlights`, `alternatives`) · returned by `run_search` / `SearchOrchestrator.run` · *Decision Page UI: not built yet*

## Design docs

- [System architecture](docs/architecture.md) — process, services, ranking and alternatives
- [Data model](docs/data-model.md) — domain objects (entities + value objects), not a price warehouse
- [Data source strategy](docs/data-source-strategy.md) — catalog vs live adapters vs FX vs fee rules
- [Initial UI concept](docs/ui-concept.md) — welcome, confirm popup, loading, Decision Page
- [Proposed algorithm](docs/proposed-algorithm.md) — weighted scoring, missing-data handling, explanations, alternative guardrails
- [Process framework](docs/process-framework-and-algorithm.md) — STEP flow from first page to Decision Page
- [Project layout](docs/project-layout.md) — Python package map and current backend status
