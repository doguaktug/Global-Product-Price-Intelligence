"""FastAPI application entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gp_price_intel import __version__
from gp_price_intel.api.routes import router
from gp_price_intel.config import get_settings

_PACKAGED_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


def resolve_web_dir() -> Path:
    """
    Prefer the git checkout's ``src/.../web`` over a stale copy in site-packages.

    ``pip install .`` (no ``-e``) freezes HTML/JS into the venv. After ``git pull``
    that copy is what uvicorn keeps serving unless we look next to the repo.
    """
    here = Path.cwd().resolve()
    for _ in range(6):
        candidate = here / "src" / "gp_price_intel" / "web"
        if (candidate / "index.html").is_file():
            return candidate
        if here.parent == here:
            break
        here = here.parent
    return _PACKAGED_WEB_DIR


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


def _asset_version(web_dir: Path) -> str:
    newest = 0
    for name in ("index.html", "app.js", "styles.css"):
        path = web_dir / name
        if path.is_file():
            newest = max(newest, int(path.stat().st_mtime))
    return str(newest)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Global Product Price Intelligence",
        version=__version__,
        description="Decision-support API for cross-border product price comparison.",
    )
    app.include_router(router)
    web_dir = resolve_web_dir()

    @app.get("/health")
    def health() -> dict[str, object]:
        settings = get_settings()
        index = web_dir / "index.html"
        html = index.read_text(encoding="utf-8") if index.is_file() else ""
        return {
            "status": "ok",
            "version": __version__,
            "default_country": settings.default_destination_country,
            "default_currency": settings.default_reference_currency,
            "web_dir": str(web_dir),
            "used_filter": 'id="include-used"' in html,
        }

    @app.get("/")
    def index() -> HTMLResponse:
        html = (web_dir / "index.html").read_text(encoding="utf-8")
        ver = _asset_version(web_dir)
        html = html.replace("/ui/styles.css", f"/ui/styles.css?v={ver}")
        html = html.replace("/ui/app.js", f"/ui/app.js?v={ver}")
        return HTMLResponse(html, headers=_NO_STORE)

    app.mount("/ui", NoCacheStaticFiles(directory=web_dir), name="ui")
    return app


app = create_app()


def run() -> None:
    import os

    import uvicorn

    uvicorn.run(
        "gp_price_intel.api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
        reload_dirs=["src"],
        app_dir="src",
    )
