# Agents

AI agents used during development of this repository:

- **Cursor Agent** (`cursoragent@cursor.com`) — scaffolding, implementation, tests, and documentation

## Cursor Cloud specific instructions

Python 3.11+ prototype under `src/gp_price_intel/`.

### Install

```bash
pip install -e ".[dev]"
cp .env.example .env
```

### Test

```bash
pytest
```

### Run API locally

```bash
uvicorn gp_price_intel.api.main:app --reload
```

Health check: `GET http://127.0.0.1:8000/health`

### Layout

- `src/gp_price_intel/` — application package (domain, catalog, normalize, API, pipeline stubs)
- `data/catalog/` — seed categories, families, variants
- `tests/` — pytest suite
- `docs/` — architecture and design documents

Most pipeline modules beyond catalog normalization are still stubs. See [docs/project-layout.md](docs/project-layout.md).
