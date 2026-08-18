# Global Product Price Intelligence

Prototype decision-support app: research a product across countries and sources, normalize offers, compare **total landed cost** and specs, and recommend the best choice **with an explanation**.

Not a conventional price-comparison site. The core question:

> Given the available global options, which product/offer is the best choice for the user, and why?

## Process (high level)

1. Normalize the query against a small reference catalog  
2. **Confirm model/specs with the user** when ambiguous or invalid (e.g. 600 GB → 512 GB or 1 TB?)  
3. Acquire live worldwide offers  
4. Match exact products (keep different specs separate)  
5. Convert with live FX, then add **shipping, border tax, registration and similar fees**  
6. Rank by user preference weights on landed cost and quality signals  
7. **Build reasoning** for each highlighted choice  
8. Suggest **close alternatives** carefully (same product different specs, or a comparable different product)  
9. Present best landed price / best for you / best rated + alternatives

## Design docs

- [System architecture](docs/architecture.md) — process, services, ranking and alternatives
- [Data model](docs/data-model.md) — domain objects (entities + value objects), not a price warehouse
