from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .engine import DiscoveryEngine
from .enrichment import EnrichmentRouter
from .models import SearchOptions
from .storage import SearchStore

logger = logging.getLogger(__name__)


class ConferenceScoutWorker:
    def __init__(
        self,
        store: SearchStore | None = None,
        engine: DiscoveryEngine | None = None,
        enricher: EnrichmentRouter | None = None,
        settings: Settings | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.store = store or SearchStore(settings=self.settings)
        self.engine = engine or DiscoveryEngine()
        self.enricher = enricher or EnrichmentRouter()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def run_once(self) -> bool:
        search = self.store.claim_search(self.worker_id, self.settings.worker_lease_seconds)
        if search:
            self._run_search(search)
            return True
        enrichment = self.store.claim_enrichment(self.worker_id, self.settings.worker_lease_seconds)
        if enrichment:
            slot: int | None = None
            if enrichment["mode"] in {"deep", "tier3"}:
                slot = self.store.acquire_provider_slot(
                    "contact_finder", self.worker_id, self.settings.worker_lease_seconds
                )
                if slot is None:
                    self.store.defer_enrichment(
                        enrichment["id"], enrichment["customer_id"], self.worker_id
                    )
                    return False
            try:
                self._run_enrichment(enrichment)
            finally:
                if slot is not None:
                    self.store.release_provider_slot("contact_finder", slot, self.worker_id)
            return True
        return False

    def run_until_idle(self, maximum_jobs: int = 100) -> int:
        completed = 0
        while completed < maximum_jobs and self.run_once():
            completed += 1
        return completed

    def run_forever(self) -> None:
        while True:
            try:
                worked = self.run_once()
            except Exception:
                logger.exception("Worker claim loop failed; retrying")
                worked = False
            if not worked:
                time.sleep(max(0.1, self.settings.worker_poll_seconds))

    def _run_search(self, job: dict[str, Any]) -> None:
        try:
            options = SearchOptions.create(**job["options"])
            results = self.engine.search(options)
            start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            used = self.store.count_usage_since(
                job["customer_id"], "contact_enrichment", start_of_day
            )
            remaining = max(0, self.settings.enrichment_daily_limit - used)
            self.store.complete(
                job["search_id"],
                results,
                job["customer_id"],
                auto_enrichment_mode=options.enrichment_mode,
                max_enrichments=min(options.max_enrichments, remaining),
                worker_id=self.worker_id,
            )
        except Exception:
            logger.exception("Conference search worker failed for %s", job["search_id"])
            self.store.fail_search(
                job["search_id"],
                job["customer_id"],
                "Search processing failed",
                self.worker_id,
            )

    def _run_enrichment(self, job: dict[str, Any]) -> None:
        conference = self.store.get_conference(job["result_id"], job["customer_id"])
        if conference is None:
            self.store.fail_enrichment(
                job["id"],
                job["customer_id"],
                "Conference result not found",
                self.worker_id,
            )
            return
        try:
            evidence, attempts = self.enricher.enrich(conference, job["mode"])
            for attempt in attempts:
                self.store.record_provider_attempt(
                    job,
                    attempt.provider,
                    attempt.strategy,
                    attempt.status,
                    attempt.duration_ms,
                    attempt.evidence.source_urls,
                    attempt.evidence.as_dict(),
                    attempt.error,
                    int(attempt.evidence.raw.get("billable_units", 0)),
                    self.worker_id,
                )
            attempted = [attempt for attempt in attempts if attempt.status != "skipped"]
            if attempted and all(attempt.status == "failed" for attempt in attempted):
                raise RuntimeError("All configured enrichment providers failed")
            if evidence.found_anything():
                self.store.update_conference_contact(
                    job["result_id"],
                    job["customer_id"],
                    evidence.as_dict(),
                    job_id=job["id"],
                    worker_id=self.worker_id,
                )
            self.store.finish_enrichment(
                job["id"], job["customer_id"], self.worker_id, evidence.found_anything()
            )
        except Exception:
            logger.exception("Contact enrichment worker failed for %s", job["id"])
            self.store.fail_enrichment(
                job["id"],
                job["customer_id"],
                "Enrichment processing failed",
                self.worker_id,
            )


def main() -> None:
    logging.basicConfig(level=os.getenv("CONFERENCE_SCOUT_LOG_LEVEL", "INFO"))
    ConferenceScoutWorker().run_forever()
