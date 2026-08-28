from concurrent.futures import ThreadPoolExecutor

import pytest

from conference_scout_mcp.config import Settings
from conference_scout_mcp.models import Conference
from conference_scout_mcp.service import ConferenceScoutService
from conference_scout_mcp.storage import SearchStore


class FakeEngine:
    def search(self, options):
        return [Conference(name="AI Summit", url="https://example.com", source="test")]


def test_service_runs_and_persists(tmp_path) -> None:
    service = ConferenceScoutService(
        store=SearchStore(tmp_path / "test.db"),
        engine=FakeEngine(),
        executor=ThreadPoolExecutor(max_workers=1),
    )
    started = service.start_search(["AI"])
    search_id = started["search_id"]
    future = service.executor.submit(lambda: None)
    future.result(timeout=2)

    status = service.status(search_id)
    assert status["status"] == "completed"
    payload = service.results(search_id)
    assert payload["results_count"] == 1
    assert payload["results"][0]["name"] == "AI Summit"


def test_delete_search(tmp_path) -> None:
    service = ConferenceScoutService(
        store=SearchStore(tmp_path / "test.db"),
        engine=FakeEngine(),
        executor=ThreadPoolExecutor(max_workers=1),
    )
    started = service.start_search(["AI"])
    service.executor.submit(lambda: None).result(timeout=2)
    assert service.delete(started["search_id"])["deleted"] is True


def test_idempotent_retries_work_after_quota_is_reserved(tmp_path) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "quota.db"),
        embedded_worker=False,
        search_daily_limit=1,
        enrichment_daily_limit=1,
    )
    store = SearchStore(tmp_path / "quota.db", settings=settings)
    service = ConferenceScoutService(store=store, engine=FakeEngine(), settings=settings)

    first = service.start_search(["AI"], idempotency_key="same-request")
    retry = service.start_search(["AI"], idempotency_key="same-request")
    assert retry["search_id"] == first["search_id"]
    with pytest.raises(ValueError, match="different search options"):
        service.start_search(["Security"], idempotency_key="same-request")
    with pytest.raises(PermissionError, match="quota"):
        service.start_search(["AI"], idempotency_key="different-request")

    [result_id] = store.complete(
        first["search_id"], [Conference(name="AI Summit", url="https://example.com")]
    )
    first_enrichment = service.enrich(result_id)
    retry_enrichment = service.enrich(result_id)
    assert retry_enrichment["job_id"] == first_enrichment["job_id"]
