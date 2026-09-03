# Project layout

Python package under `src/gp_price_intel/`. Service boundaries match [architecture.md](architecture.md).

```
src/gp_price_intel/
  domain/         # Pydantic domain model
  catalog/        # Seed catalog repository
  normalize/      # Query → confirmation prompts
  adapters/       # SourceAdapter interface + fixture stub
  fx/             # FX (original Money never overwritten)
  landed_cost/    # Landed-cost estimates
  matching/       # Offer ↔ product matching
  ranking/        # Weighted scoring
  explanation/    # Why text
  alternatives/   # Guarded alternatives
  orchestrator/   # Search session pipeline
  api/            # FastAPI
data/
  catalog/        # categories.json, families.json, variants.json
  sources/        # sources.json
  fixtures/       # offer snapshots for demos
tests/            # pytest smoke tests
```

Week 2 progress: **normalize/** fuzzy matching + confirmation; **matching/** SKU/GTIN identity; **adapters/** eBay + fixtures; pipeline wired through FX, landed cost, ranking, highlights.
