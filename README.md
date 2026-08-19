# Global Product Price Intelligence

Prototype decision-support app: research a product across countries and sources, normalize offers, compare **total landed cost** and specs, and recommend the best choice **with an explanation**.

Not a conventional price-comparison site. The core question:

> Given the available global options, which product/offer is the best choice for the user, and why?

## Process (high level)

1. User enters a product  
2. **User selects preference weights** (optional sliders; country/currency: TR+TRY → geo if permitted → manual next to sliders; later overwrites earlier)  
3. Normalize the query against a small reference catalog  
4. **Confirm popup only if needed** — skip when the catalog already has a unique valid match  
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
