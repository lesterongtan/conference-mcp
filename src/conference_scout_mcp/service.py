from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import Settings
from .engine import DiscoveryEngine
from .models import SearchOptions
from .storage import SearchStore
from .worker import ConferenceScoutWorker


class ConferenceScoutService:
    def __init__(
        self,
        store: SearchStore | None = None,
        engine: DiscoveryEngine | None = None,
        executor: ThreadPoolExecutor | None = None,
        settings: Settings | None = None,
        worker: ConferenceScoutWorker | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.store = store or SearchStore(settings=self.settings)
        self.engine = engine or DiscoveryEngine(settings=self.settings)
        self.worker = worker or ConferenceScoutWorker(
            store=self.store, engine=self.engine, settings=self.settings
        )
        if executor is not None:
            self.executor = executor
        elif self.settings.embedded_worker:
            self.executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="conference-worker"
            )
        else:
            self.executor = None

    def start_search(
        self,
        keywords: list[str],
        geography: str = "",
        max_results: int = 25,
        min_days_out: int = 0,
        max_days_out: int = 365,
        open_cfp_only: bool = False,
        enrichment_mode: str = "basic",
        max_enrichments: int = 5,
        customer_id: str = "local",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        options = SearchOptions.create(
            keywords=keywords,
            geography=geography,
            max_results=max_results,
            min_days_out=min_days_out,
            max_days_out=max_days_out,
            open_cfp_only=open_cfp_only,
            enrichment_mode=enrichment_mode,
            max_enrichments=max_enrichments,
        )
        search_id = self.store.create_search(options, customer_id, idempotency_key)
        if self.executor is not None:
            self.executor.submit(self.worker.run_until_idle)
        return {
            "search_id": search_id,
            "status": self.store.get_search(search_id, customer_id)["status"],
            "options": options.as_dict(),
            "next_action": (
                "Call get_search_status with this search_id until it is completed or error."
            ),
        }

    def status(self, search_id: str, customer_id: str = "local") -> dict[str, Any]:
        result = self.store.get_search(search_id, customer_id)
        if result is None:
            raise ValueError("Search not found")
        if result["status"] == "completed":
            result["next_action"] = "Call get_search_results to retrieve the ranked conferences."
        elif result["status"] in {"queued", "running"}:
            result["next_action"] = (
                "Check this search again later; do not start a duplicate search."
            )
        return result

    def results(
        self, search_id: str, limit: int = 50, offset: int = 0, customer_id: str = "local"
    ) -> dict[str, Any]:
        status = self.status(search_id, customer_id)
        return {
            **status,
            "results": self.store.get_results(search_id, limit, offset, customer_id),
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
        }

    def conference(self, result_id: str, customer_id: str = "local") -> dict[str, Any]:
        result = self.store.get_conference(result_id, customer_id)
        if result is None:
            raise ValueError("Conference result not found")
        return result

    def enrich(
        self, result_id: str, mode: str = "deep", customer_id: str = "local"
    ) -> dict[str, Any]:
        job_id = self.store.enqueue_enrichment(result_id, customer_id, mode)
        if self.executor is not None:
            self.executor.submit(self.worker.run_until_idle)
        status = self.store.get_enrichment(job_id, customer_id)
        return {
            "job_id": job_id,
            "result_id": result_id,
            "status": status["status"],
            "next_action": "Call get_enrichment_status with this job_id.",
        }

    def enrichment_status(self, job_id: str, customer_id: str = "local") -> dict[str, Any]:
        result = self.store.get_enrichment(job_id, customer_id)
        if result is None:
            raise ValueError("Enrichment job not found")
        return result

    def provider_status(self) -> dict[str, Any]:
        status = self.store.get_worker_provider_status(
            active_seconds=max(90, self.settings.worker_heartbeat_seconds * 3)
        )
        status["note"] = (
            "Provider secrets are never returned. Browser Use/RTRVR run behind Contact Finder."
        )
        return status

    def delete(self, search_id: str, customer_id: str = "local") -> dict[str, Any]:
        return {"search_id": search_id, "deleted": self.store.delete_search(search_id, customer_id)}
