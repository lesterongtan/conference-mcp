import json
import sqlite3
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from conference_scout_mcp.config import Settings
from conference_scout_mcp.models import Conference, SearchOptions
from conference_scout_mcp.storage import (
    SearchStore,
    conferences,
    enrichment_jobs,
    provider_attempts,
    searches,
)


def make_store(tmp_path) -> SearchStore:
    settings = Settings(
        database_url="",
        sqlite_path=str(tmp_path / "service.db"),
        bootstrap_customer_id="local",
        api_key_pepper="test-pepper",
    )
    return SearchStore(tmp_path / "service.db", settings=settings)


def test_api_keys_and_tenant_isolation(tmp_path) -> None:
    store = make_store(tmp_path)
    store.ensure_customer("tenant-a", "Tenant A")
    store.ensure_customer("tenant-b", "Tenant B")
    store.create_api_key("tenant-a", "csa_test_key_for_tenant_a")
    store.create_api_key("tenant-b", "csb_test_key_for_tenant_b")

    assert store.verify_api_key("csa_test_key_for_tenant_a") == "tenant-a"
    assert store.verify_api_key("invalid-key") is None

    search_id = store.create_search(SearchOptions.create(["AI"]), "tenant-a")
    store.complete(search_id, [Conference(name="AI Summit", url="https://example.com")], "tenant-a")
    result_id = store.get_results(search_id, customer_id="tenant-a")[0]["id"]

    assert store.get_search(search_id, "tenant-b") is None
    assert store.get_conference(result_id, "tenant-b") is None
    assert store.delete_search(search_id, "tenant-b") is False
    assert store.get_conference(result_id, "tenant-a")["name"] == "AI Summit"


def test_search_idempotency_is_scoped_per_tenant(tmp_path) -> None:
    store = make_store(tmp_path)
    store.ensure_customer("tenant-a")
    store.ensure_customer("tenant-b")
    options = SearchOptions.create(["healthcare AI"])

    first = store.create_search(options, "tenant-a", "same-request")
    duplicate = store.create_search(options, "tenant-a", "same-request")
    other_tenant = store.create_search(options, "tenant-b", "same-request")

    assert duplicate == first
    assert other_tenant != first


def test_expired_search_lease_can_be_reclaimed(tmp_path) -> None:
    store = make_store(tmp_path)
    search_id = store.create_search(SearchOptions.create(["AI"]))

    first = store.claim_search("worker-one", lease_seconds=30)
    assert first and first["search_id"] == search_id
    assert store.claim_search("worker-two", lease_seconds=30) is None

    with store.engine.begin() as connection:
        connection.execute(
            update(searches)
            .where(searches.c.id == search_id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    reclaimed = store.claim_search("worker-two", lease_seconds=30)
    assert reclaimed and reclaimed["search_id"] == search_id


def test_contact_updates_are_additive_and_jobs_are_idempotent(tmp_path) -> None:
    store = make_store(tmp_path)
    search_id = store.create_search(SearchOptions.create(["AI"]))
    [result_id] = store.complete(
        search_id,
        [Conference(name="AI Summit", url="https://example.com", contact_name="Original")],
    )

    first_job = store.enqueue_enrichment(result_id, "local", "deep")
    duplicate_job = store.enqueue_enrichment(result_id, "local", "tier3")
    assert duplicate_job == first_job

    store.update_conference_contact(
        result_id,
        "local",
        {
            "contact_name": "Replacement",
            "contact_email": "organizer@example.org",
            "provider": "test",
            "confidence": 0.8,
            "source_urls": ["https://example.com/contact"],
        },
    )
    result = store.get_conference(result_id)
    assert result["contact_name"] == "Original"
    assert result["contact_email"] == "organizer@example.org"
    assert result["contact_source"] == "test"
    assert "https://example.com/contact" in result["evidence"]


def test_v01_sqlite_data_is_migrated_without_loss(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    search_id = "00000000-0000-0000-0000-000000000001"
    result_id = "00000000-0000-0000-0000-000000000002"
    now = datetime.now(UTC).isoformat()
    payload = Conference(name="Legacy Summit", url="https://legacy.test", id=result_id).as_dict()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE searches (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, options_json TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE conferences (
                id TEXT PRIMARY KEY, search_id TEXT NOT NULL, rank INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO searches VALUES (?, 'completed', ?, '', ?, ?)",
            (search_id, json.dumps(SearchOptions.create(["AI"]).as_dict()), now, now),
        )
        connection.execute(
            "INSERT INTO conferences VALUES (?, ?, 1, ?)",
            (result_id, search_id, json.dumps(payload)),
        )

    store = SearchStore(path, settings=Settings(bootstrap_customer_id="local"))

    assert store.get_search(search_id)["results_count"] == 1
    assert store.get_conference(result_id)["name"] == "Legacy Summit"


def test_queued_work_reserves_daily_quota_and_idempotency_does_not_double_charge(
    tmp_path,
) -> None:
    settings = Settings(
        sqlite_path=str(tmp_path / "quota.db"),
        search_daily_limit=1,
        enrichment_daily_limit=1,
    )
    store = SearchStore(tmp_path / "quota.db", settings=settings)
    options = SearchOptions.create(["AI"])

    first = store.create_search(options, idempotency_key="request-1")
    assert store.create_search(options, idempotency_key="request-1") == first
    try:
        store.create_search(options, idempotency_key="request-2")
    except PermissionError as exc:
        assert "quota" in str(exc).lower()
    else:
        raise AssertionError("A second queued search should exceed the daily quota")


def test_sqlite_delete_cascades_to_results_enrichments_and_attempts(tmp_path) -> None:
    store = make_store(tmp_path)
    search_id = store.create_search(SearchOptions.create(["AI"]))
    [result_id] = store.complete(
        search_id, [Conference(name="AI Summit", url="https://example.com")]
    )
    job_id = store.enqueue_enrichment(result_id, "local", "deep")
    job = store.claim_enrichment("worker")
    assert job and job["id"] == job_id
    store.record_provider_attempt(job, "test", "deep", "completed", 1, [], {}, "", 0)

    assert store.delete_search(search_id) is True
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(conferences)).scalar_one() == 0
        assert (
            connection.execute(select(func.count()).select_from(enrichment_jobs)).scalar_one() == 0
        )
        assert (
            connection.execute(select(func.count()).select_from(provider_attempts)).scalar_one()
            == 0
        )


def test_exhausted_expired_leases_are_moved_to_terminal_states(tmp_path) -> None:
    store = make_store(tmp_path)
    search_id = store.create_search(SearchOptions.create(["AI"]))
    [result_id] = store.complete(
        search_id, [Conference(name="AI Summit", url="https://example.com")]
    )
    enrichment_id = store.enqueue_enrichment(result_id, "local", "deep")
    expired = datetime.now(UTC) - timedelta(seconds=1)
    with store.engine.begin() as connection:
        connection.execute(
            update(searches)
            .where(searches.c.id == search_id)
            .values(status="running", attempts=3, max_attempts=3, lease_until=expired)
        )
        connection.execute(
            update(enrichment_jobs)
            .where(enrichment_jobs.c.id == enrichment_id)
            .values(status="running", attempts=3, max_attempts=3, lease_until=expired)
        )

    assert store.claim_search("worker") is None
    assert store.claim_enrichment("worker") is None
    assert store.get_search(search_id)["status"] == "error"
    assert store.get_enrichment(enrichment_id, "local")["status"] == "failed"


def test_basic_mode_queues_automatic_enrichment(tmp_path) -> None:
    store = make_store(tmp_path)
    search_id = store.create_search(SearchOptions.create(["AI"]))
    [result_id] = store.complete(
        search_id,
        [Conference(name="AI Summit", url="https://example.com")],
        auto_enrichment_mode="basic",
        max_enrichments=1,
    )

    assert store.get_conference(result_id)["enrichment"]["status"] == "queued"


def test_contact_finder_slots_cap_concurrency_across_workers(tmp_path) -> None:
    settings = Settings(sqlite_path=str(tmp_path / "slots.db"), contact_finder_max_concurrent=2)
    store = SearchStore(tmp_path / "slots.db", settings=settings)

    first = store.acquire_provider_slot("contact_finder", "worker-1", 60)
    second = store.acquire_provider_slot("contact_finder", "worker-2", 60)
    assert {first, second} == {1, 2}
    assert store.acquire_provider_slot("contact_finder", "worker-3", 60) is None

    store.release_provider_slot("contact_finder", first, "worker-1")
    assert store.acquire_provider_slot("contact_finder", "worker-3", 60) == first
