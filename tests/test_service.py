from concurrent.futures import ThreadPoolExecutor

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

