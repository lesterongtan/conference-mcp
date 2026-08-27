from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .engine import DiscoveryEngine
from .models import SearchOptions
from .storage import SearchStore

logger = logging.getLogger(__name__)


class ConferenceScoutService:
    def __init__(
        self,
        store: SearchStore | None = None,
        engine: DiscoveryEngine | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.store = store or SearchStore()
        self.engine = engine or DiscoveryEngine()
        self.executor = executor or ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="cfp-scout"
        )

    def start_search(
        self,
        keywords: list[str],
        geography: str = "",
        max_results: int = 25,
        min_days_out: int = 0,
        max_days_out: int = 365,
        open_cfp_only: bool = False,
    ) -> dict[str, Any]:
        options = SearchOptions.create(
            keywords=keywords,
            geography=geography,
            max_results=max_results,
            min_days_out=min_days_out,
            max_days_out=max_days_out,
            open_cfp_only=open_cfp_only,
        )
        search_id = self.store.create_search(options)
        self.executor.submit(self._run_search, search_id, options)
        return {
            "search_id": search_id,
            "status": "queued",
            "options": options.as_dict(),
            "next_action": (
                "Call get_search_status with this search_id until status is completed or error."
            ),
        }

    def _run_search(self, search_id: str, options: SearchOptions) -> None:
        self.store.set_running(search_id)
        try:
            results = self.engine.search(options)
            self.store.complete(search_id, results)
        except Exception as exc:
            logger.exception("Conference search failed: %s", search_id)
            self.store.fail(search_id, str(exc))

    def status(self, search_id: str) -> dict[str, Any]:
        result = self.store.get_search(search_id)
        if result is None:
            raise ValueError("Search not found")
        if result["status"] == "completed":
            result["next_action"] = "Call get_search_results to retrieve the ranked conferences."
        elif result["status"] in {"queued", "running"}:
            result["next_action"] = (
                "Check this search again later; do not start a duplicate search."
            )
        return result

    def results(self, search_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        status = self.status(search_id)
        return {
            **status,
            "results": self.store.get_results(search_id, limit=limit, offset=offset),
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
        }

    def conference(self, result_id: str) -> dict[str, Any]:
        result = self.store.get_conference(result_id)
        if result is None:
            raise ValueError("Conference result not found")
        return result

    def delete(self, search_id: str) -> dict[str, Any]:
        deleted = self.store.delete_search(search_id)
        return {"search_id": search_id, "deleted": deleted}
