from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import conference_scout_mcp.api as api_module
from conference_scout_mcp.models import Conference
from conference_scout_mcp.service import ConferenceScoutService
from conference_scout_mcp.storage import SearchStore


class FakeEngine:
    def search(self, options):
        return [Conference(name="AI Summit", url="https://summit.test", source="test")]


def test_rest_search_lifecycle(monkeypatch, tmp_path) -> None:
    service = ConferenceScoutService(
        store=SearchStore(tmp_path / "api.db"),
        engine=FakeEngine(),
        executor=ThreadPoolExecutor(max_workers=1),
    )
    monkeypatch.setattr(api_module, "get_service", lambda: service)

    with TestClient(api_module.app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json() == {"ready": True}
        started = client.post(
            "/v1/searches",
            headers={"Idempotency-Key": "api-test"},
            json={"keywords": ["AI"], "max_results": 5},
        )
        assert started.status_code == 202
        search_id = started.json()["search_id"]
        service.executor.submit(lambda: None).result(timeout=2)

        status = client.get(f"/v1/searches/{search_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"
        results = client.get(f"/v1/searches/{search_id}/results").json()
        assert results["results_count"] == 1
        providers = client.get("/v1/providers/status")
        assert providers.status_code == 200
        assert providers.json()["providers"]["confs_tech"] is True
        result_id = results["results"][0]["id"]
        assert client.get(f"/v1/conferences/{result_id}").json()["name"] == "AI Summit"

        deleted = client.delete(f"/v1/searches/{search_id}")
        assert deleted.json()["deleted"] is True
        assert client.get(f"/v1/searches/{search_id}").status_code == 404
        response = client.post(
            "/v1/searches",
            json={"keywords": ["AI"], "enrichment_mode": "unlimited"},
        )
        assert response.status_code == 400
