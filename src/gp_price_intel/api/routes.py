"""HTTP routes — thin wrappers around catalog + orchestrator."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gp_price_intel.catalog.repository import CatalogRepository
from gp_price_intel.domain.models import (
    DecisionPage,
    NormalizedQuery,
    PropertyChoice,
    SearchSession,
    UserPreferences,
)
from gp_price_intel.normalize.confirmation import ConfirmationError
from gp_price_intel.orchestrator.search import SearchOrchestrator

router = APIRouter(prefix="/api")
_catalog = CatalogRepository()
_orchestrator = SearchOrchestrator(catalog=_catalog)


class StartSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    preferences: UserPreferences | None = None


class NormalizeRequest(BaseModel):
    query: str = Field(min_length=1)


class ConfirmRequest(BaseModel):
    session: SearchSession
    choices: list[PropertyChoice] = Field(default_factory=list)


@router.get("/catalog/categories")
def list_categories() -> list[dict]:
    return [c.model_dump() for c in _catalog.list_categories()]


@router.get("/catalog/families")
def list_families(category_id: str | None = None) -> list[dict]:
    return [f.model_dump() for f in _catalog.list_families(category_id)]


@router.get("/catalog/variants")
def list_variants(family_id: str | None = None) -> list[dict]:
    return [v.model_dump() for v in _catalog.list_variants(family_id)]


@router.post("/search/start", response_model=SearchSession)
def start_search(body: StartSearchRequest) -> SearchSession:
    return _orchestrator.start_session(body.query, body.preferences)


@router.post("/search/normalize", response_model=NormalizedQuery)
def normalize_query(body: NormalizeRequest) -> NormalizedQuery:
    """Preview catalog match + confirmation prompts without creating a session."""
    return _orchestrator.preview_normalization(body.query)


@router.post("/search/confirm", response_model=SearchSession)
def confirm_search(body: ConfirmRequest) -> SearchSession:
    try:
        return _orchestrator.apply_choices(body.session, body.choices)
    except ConfirmationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/search/run", response_model=DecisionPage)
async def run_search(session: SearchSession) -> DecisionPage:
    if session.status.value == "needs_confirmation":
        raise HTTPException(
            status_code=409,
            detail="Session still needs confirmation before live search.",
        )
    return await _orchestrator.run(session)
