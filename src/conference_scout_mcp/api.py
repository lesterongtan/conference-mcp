from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Any
from urllib.parse import urlparse

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from .config import Settings
from .server import get_service, mcp

settings = Settings()
bearer = HTTPBearer(auto_error=False)


def customer_id_for_request(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    raw_key = credentials.credentials if credentials else (x_api_key or "")
    if not raw_key and not settings.require_auth:
        return settings.bootstrap_customer_id
    customer_id = get_service().store.verify_api_key(raw_key)
    if customer_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key"
        )
    return customer_id


class SearchRequest(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=8)
    geography: str = ""
    max_results: int = Field(default=25, ge=1, le=100)
    min_days_out: int = Field(default=0, ge=0)
    max_days_out: int = Field(default=365, ge=0, le=1095)
    open_cfp_only: bool = False
    enrichment_mode: str = "basic"
    max_enrichments: int = Field(default=5, ge=0, le=25)


class EnrichmentRequest(BaseModel):
    mode: str = "deep"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.http_host not in {"127.0.0.1", "localhost", "::1"} and not settings.require_auth:
        raise RuntimeError("Hosted Conference Scout refuses to start without authentication")
    public_host = (urlparse(settings.public_url).hostname or "").lower()
    if settings.http_host not in {"127.0.0.1", "localhost", "::1"} and public_host in {
        "",
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RuntimeError("Hosted Conference Scout requires CONFERENCE_SCOUT_PUBLIC_URL")
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Conference Scout API", version="0.2.0", lifespan=lifespan)
origins = [value.strip() for value in settings.cors_origins.split(",") if value.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-API-Key"],
    )


@app.get("/healthz")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "conference-scout", "version": "0.2.0"}


@app.get("/readyz")
def ready() -> dict[str, Any]:
    try:
        get_service().store.engine.connect().close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"ready": True}


@app.post("/v1/searches", status_code=202)
def start_search(
    request: SearchRequest,
    customer_id: Annotated[str, Depends(customer_id_for_request)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    try:
        return get_service().start_search(
            **request.model_dump(), customer_id=customer_id, idempotency_key=idempotency_key
        )
    except (ValueError, PermissionError) as exc:
        code = 429 if isinstance(exc, PermissionError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.get("/v1/searches/{search_id}")
def search_status(
    search_id: str, customer_id: Annotated[str, Depends(customer_id_for_request)]
) -> dict[str, Any]:
    try:
        return get_service().status(search_id, customer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/searches/{search_id}/results")
def search_results(
    search_id: str,
    customer_id: Annotated[str, Depends(customer_id_for_request)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    try:
        return get_service().results(search_id, limit, offset, customer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/conferences/{result_id}")
def conference(
    result_id: str, customer_id: Annotated[str, Depends(customer_id_for_request)]
) -> dict[str, Any]:
    try:
        return get_service().conference(result_id, customer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/conferences/{result_id}/enrich", status_code=202)
def enrich(
    result_id: str,
    request: EnrichmentRequest,
    customer_id: Annotated[str, Depends(customer_id_for_request)],
) -> dict[str, Any]:
    try:
        return get_service().enrich(result_id, request.mode, customer_id)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@app.get("/v1/enrichments/{job_id}")
def enrichment_status(
    job_id: str, customer_id: Annotated[str, Depends(customer_id_for_request)]
) -> dict[str, Any]:
    try:
        return get_service().enrichment_status(job_id, customer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/v1/searches/{search_id}")
def delete_search(
    search_id: str, customer_id: Annotated[str, Depends(customer_id_for_request)]
) -> dict[str, Any]:
    return get_service().delete(search_id, customer_id)


app.mount("/", mcp.streamable_http_app())


def main() -> None:
    uvicorn.run(
        "conference_scout_mcp.api:app",
        host=settings.http_host,
        port=int(os.getenv("CONFERENCE_SCOUT_HTTP_PORT", "8765")),
    )
