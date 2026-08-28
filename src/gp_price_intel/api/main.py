"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from gp_price_intel import __version__
from gp_price_intel.api.routes import router
from gp_price_intel.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="Global Product Price Intelligence",
        version=__version__,
        description="Decision-support API skeleton (Week 2).",
    )
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        settings = get_settings()
        return {
            "status": "ok",
            "version": __version__,
            "default_country": settings.default_destination_country,
            "default_currency": settings.default_reference_currency,
        }

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gp_price_intel.api.main:app", host="0.0.0.0", port=8000, reload=True)
