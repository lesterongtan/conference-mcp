from __future__ import annotations

import argparse
import logging
import os
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings
from .service import ConferenceScoutService

logging.basicConfig(
    level=os.getenv("CONFERENCE_SCOUT_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings = Settings()
_service: ConferenceScoutService | None = None


def get_service() -> ConferenceScoutService:
    global _service
    if _service is None:
        _service = ConferenceScoutService(settings=settings)
    return _service


class ApiKeyTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        customer_id = get_service().store.verify_api_key(token)
        if customer_id is None:
            return None
        return AccessToken(
            token="redacted",
            client_id=customer_id,
            subject=customer_id,
            scopes=["conference-scout"],
        )


auth_settings = None
token_verifier = None
if settings.require_auth:
    auth_settings = AuthSettings(
        issuer_url=settings.public_url,
        resource_server_url=f"{settings.public_url.rstrip('/')}/mcp",
        required_scopes=["conference-scout"],
    )
    token_verifier = ApiKeyTokenVerifier()

public = urlparse(settings.public_url)
public_host = public.netloc or "127.0.0.1:8765"
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(
        dict.fromkeys([public_host, public.hostname or "", "127.0.0.1:*", "localhost:*"])
    ),
    allowed_origins=[f"{public.scheme}://{public_host}"] if public.scheme else [],
)

mcp = FastMCP(
    "Conference Scout",
    host=os.getenv("CONFERENCE_SCOUT_HTTP_HOST", "127.0.0.1"),
    port=int(os.getenv("CONFERENCE_SCOUT_HTTP_PORT", "8765")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    auth=auth_settings,
    token_verifier=token_verifier,
    transport_security=transport_security,
)


def current_customer_id() -> str:
    access_token = get_access_token()
    if access_token and access_token.subject:
        return access_token.subject
    if settings.require_auth:
        raise PermissionError("Authentication is required")
    return settings.bootstrap_customer_id


@mcp.tool()
def start_conference_search(
    keywords: list[str],
    geography: str = "",
    max_results: int = 25,
    min_days_out: int = 0,
    max_days_out: int = 365,
    open_cfp_only: bool = False,
    enrichment_mode: str = "basic",
    max_enrichments: int = 5,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Start an asynchronous keyword-driven conference and CFP search."""
    return get_service().start_search(
        keywords,
        geography,
        max_results,
        min_days_out,
        max_days_out,
        open_cfp_only,
        enrichment_mode,
        max_enrichments,
        current_customer_id(),
        idempotency_key or None,
    )


@mcp.tool()
def get_search_status(search_id: str) -> dict[str, Any]:
    """Get progress and result count for an owned conference search."""
    return get_service().status(search_id, current_customer_id())


@mcp.tool()
def get_search_results(search_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Return ranked results and source evidence for an owned search."""
    return get_service().results(search_id, limit, offset, current_customer_id())


@mcp.tool()
def get_conference(result_id: str) -> dict[str, Any]:
    """Return one owned conference result, contact data, evidence and enrichment state."""
    return get_service().conference(result_id, current_customer_id())


@mcp.tool()
def enrich_conference_contact(result_id: str, mode: str = "deep") -> dict[str, Any]:
    """Queue contact enrichment. Modes are basic, standard, deep, or tier3."""
    return get_service().enrich(result_id, mode, current_customer_id())


@mcp.tool()
def get_enrichment_status(job_id: str) -> dict[str, Any]:
    """Get the status of an owned contact-enrichment job."""
    return get_service().enrichment_status(job_id, current_customer_id())


@mcp.tool()
def delete_search(search_id: str) -> dict[str, Any]:
    """Delete one owned search and all related results and attempts."""
    return get_service().delete(search_id, current_customer_id())


def main() -> None:
    parser = argparse.ArgumentParser(description="Conference Scout MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=os.getenv("CONFERENCE_SCOUT_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
