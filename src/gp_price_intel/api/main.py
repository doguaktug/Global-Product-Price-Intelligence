"""FastAPI application entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gp_price_intel import __version__
from gp_price_intel.api.routes import router
from gp_price_intel.config import get_settings

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Global Product Price Intelligence",
        version=__version__,
        description="Decision-support API for cross-border product price comparison.",
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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/ui", StaticFiles(directory=WEB_DIR), name="ui")
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gp_price_intel.api.main:app", host="0.0.0.0", port=8000, reload=True)
