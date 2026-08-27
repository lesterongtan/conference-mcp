from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .service import ConferenceScoutService

logging.basicConfig(
    level=os.getenv("CONFERENCE_SCOUT_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

mcp = FastMCP(
    "Conference Scout",
    host=os.getenv("CONFERENCE_SCOUT_HTTP_HOST", "127.0.0.1"),
    port=int(os.getenv("CONFERENCE_SCOUT_HTTP_PORT", "8765")),
)
_service: ConferenceScoutService | None = None


def get_service() -> ConferenceScoutService:
    global _service
    if _service is None:
        _service = ConferenceScoutService()
    return _service


@mcp.tool()
def start_conference_search(
    keywords: list[str],
    geography: str = "",
    max_results: int = 25,
    min_days_out: int = 0,
    max_days_out: int = 365,
    open_cfp_only: bool = False,
) -> dict[str, Any]:
    """Start a keyword-driven conference and CFP search.

    Use 1-8 concise topic or industry phrases. The search runs asynchronously
    and returns a search_id; monitor that same search rather than starting duplicates.
    """
    return get_service().start_search(
        keywords=keywords,
        geography=geography,
        max_results=max_results,
        min_days_out=min_days_out,
        max_days_out=max_days_out,
        open_cfp_only=open_cfp_only,
    )


@mcp.tool()
def get_search_status(search_id: str) -> dict[str, Any]:
    """Get progress and result count for an existing conference search."""
    return get_service().status(search_id)


@mcp.tool()
def get_search_results(search_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Return ranked conference results for a completed search.

    Each result includes source evidence and may include CFP status, deadline,
    submission URL, event date, location, and public contact information.
    """
    return get_service().results(search_id, limit=limit, offset=offset)


@mcp.tool()
def get_conference(result_id: str) -> dict[str, Any]:
    """Return one stored conference result and its source evidence."""
    return get_service().conference(result_id)


@mcp.tool()
def delete_search(search_id: str) -> dict[str, Any]:
    """Delete one stored search and all of its conference results."""
    return get_service().delete(search_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Conference Scout MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=os.getenv("CONFERENCE_SCOUT_TRANSPORT", "stdio"),
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
