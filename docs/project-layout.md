# Project layout

Python package under `src/gp_price_intel/`. Service boundaries match [architecture.md](architecture.md).

```
src/gp_price_intel/
  domain/         # Pydantic domain model
  catalog/        # Seed catalog repository
  normalize/      # Query → confirmation prompts
  adapters/       # SourceAdapter + eBay + fixtures + registry
  fx/             # Frankfurter FX (original Money never overwritten)
  landed_cost/    # Shipping / duty estimates
  matching/       # Offer ↔ product matching (SKU / GTIN / attributes)
  ranking/        # Weighted scoring, confidence, highlights
  explanation/    # Why text + reliability caveats
  alternatives/   # Guarded alternatives
  orchestrator/   # Search session pipeline
  api/            # FastAPI
data/
  catalog/        # categories.json, families.json, variants.json
  sources/        # sources.json (reliability registry)
  fixtures/       # offer snapshots for non-API markets
tests/            # pytest (invariants + pipeline)
```

**Backend status:** normalize + confirmation; two-tier offer matching; eBay Browse API + multi-country fixtures; Frankfurter FX; landed cost with per-lane shipping; ranking with a confidence floor for highlights; explanations; ranked and badged alternatives. No UI — the Decision Page is served as JSON.
