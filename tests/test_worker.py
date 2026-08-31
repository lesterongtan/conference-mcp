import httpx
import respx

from conference_scout_mcp.config import Settings
from conference_scout_mcp.enrichment import ProviderRun
from conference_scout_mcp.models import Conference, ContactEvidence, SearchOptions
from conference_scout_mcp.storage import SearchStore
from conference_scout_mcp.worker import ConferenceScoutWorker


class FakeEngine:
    def search(self, options):
        return [Conference(name="AI Summit", url="https://summit.test")]


class FailingEngine:
    def search(self, options):
        raise RuntimeError("provider secret must not escape")


class FakeRouter:
    def enrich(self, conference, mode):
        evidence = ContactEvidence(
            contact_name="Alex Smith",
            contact_email="alex@aisummit.org",
            provider="fake_contact",
            confidence=0.9,
            source_urls=["https://summit.test/contact"],
        )
        return evidence, [ProviderRun("fake_contact", mode, "completed", 12, evidence)]


def settings_for(tmp_path) -> Settings:
    return Settings(
        database_url="",
        sqlite_path=str(tmp_path / "worker.db"),
        bootstrap_customer_id="local",
        embedded_worker=False,
    )


def test_worker_runs_durable_search_then_enrichment(tmp_path) -> None:
    settings = settings_for(tmp_path)
    store = SearchStore(tmp_path / "worker.db", settings=settings)
    options = SearchOptions.create(["AI"], enrichment_mode="standard", max_enrichments=1)
    search_id = store.create_search(options)
    worker = ConferenceScoutWorker(
        store=store,
        engine=FakeEngine(),
        enricher=FakeRouter(),
        settings=settings,
        worker_id="test-worker",
    )

    assert worker.run_once() is True
    assert store.get_search(search_id)["status"] == "completed"
    result_id = store.get_results(search_id)[0]["id"]
    assert store.get_conference(result_id)["enrichment"]["status"] == "queued"

    assert worker.run_once() is True
    result = store.get_conference(result_id)
    assert result["contact_name"] == "Alex Smith"
    assert result["contact_email"] == "alex@aisummit.org"
    assert result["enrichment"]["status"] == "completed"


def test_worker_retries_and_sanitizes_search_failure(tmp_path) -> None:
    settings = settings_for(tmp_path)
    store = SearchStore(tmp_path / "worker.db", settings=settings)
    search_id = store.create_search(SearchOptions.create(["AI"]))
    worker = ConferenceScoutWorker(
        store=store, engine=FailingEngine(), settings=settings, worker_id="test-worker"
    )

    assert worker.run_once() is True
    assert worker.run_once() is True
    assert worker.run_once() is True
    status = store.get_search(search_id)
    assert status["status"] == "error"
    assert status["error"] == "Search processing failed"
    assert "secret" not in status["error"]


@respx.mock
def test_worker_heartbeat_reports_provider_configuration(tmp_path) -> None:
    respx.get("https://contact-finder.example/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "service": "contact-finder",
                "backend": "rtrvr",
                "browser_use_configured": True,
                "rtrvr_configured": True,
            },
        )
    )
    settings = Settings(
        database_url="",
        sqlite_path=str(tmp_path / "providers.db"),
        bootstrap_customer_id="local",
        embedded_worker=False,
        serper_api_key="serper-key",
        apify_api_token="apify-key",
        apify_actor_id="zen-studio~10times-events-scraper",
        firecrawl_api_key="firecrawl-key",
        contact_finder_url="https://contact-finder.example",
    )
    store = SearchStore(tmp_path / "providers.db", settings=settings)
    worker = ConferenceScoutWorker(
        store=store,
        engine=FakeEngine(),
        settings=settings,
        worker_id="provider-worker",
    )

    assert worker.run_once() is False
    status = store.get_worker_provider_status()

    assert status["active_workers"] == 1
    assert status["providers"]["serper"] is True
    assert status["providers"]["apify_directory"] is True
    assert status["providers"]["firecrawl"] is True
    assert status["providers"]["contact_finder"] is True
    contact_status = status["workers"][0]["capabilities"]["contact_finder_status"]
    assert contact_status["reachable"] is True
    assert contact_status["backend"] == "rtrvr"
