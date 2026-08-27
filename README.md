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

Most pipeline modules are **stubs** ready to implement in Week 2. Catalog seed data and the API shell already load.

## Process (high level)

1. User enters a product  
2. **User selects preference weights** (optional sliders; country/currency: TR+TRY → geo if permitted → manual next to sliders; later overwrites earlier)  
3. Normalize the query against a small reference catalog  
4. **Confirm popup only if needed** — skip when fully specified; missing identity props (e.g. storage on `Samsung S26`) require a choice; non-scoring props (e.g. colour) may be **Not important** (search all)  
5. Loading (animation / fun facts over real work)  
6. Acquire live worldwide offers  
7. Match exact products (keep different specs separate)  
8. Convert with live FX, then add **shipping, border tax, registration and similar fees**  
9. Rank by the user’s weights on landed cost and quality signals  
10. **Build reasoning** for each highlighted choice  
11. Suggest **close alternatives** carefully (same product different specs, or a comparable different product)  
12. Present the **Decision Page** (why, FX, landed-cost add-ons, best landed / best for you / best rated + alternatives)

## Design docs

- [System architecture](docs/architecture.md) — process, services, ranking and alternatives
- [Data model](docs/data-model.md) — domain objects (entities + value objects), not a price warehouse
- [Data source strategy](docs/data-source-strategy.md) — catalog vs live adapters vs FX vs fee rules
- [Initial UI concept](docs/ui-concept.md) — welcome, confirm popup, loading, Decision Page
- [Proposed algorithm](docs/proposed-algorithm.md) — weighted scoring, missing-data handling, explanations, alternative guardrails
- [Process framework](docs/process-framework-and-algorithm.md) — STEP flow from first page to Decision Page
- [Project layout](docs/project-layout.md) — Python package map for Week 2
