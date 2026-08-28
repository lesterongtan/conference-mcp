from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    case,
    create_engine,
    delete,
    event,
    func,
    insert,
    inspect,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .models import Conference, SearchOptions
from .security import hash_api_key

metadata = MetaData()

customers = Table(
    "customers",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(100), nullable=False),
    Column("key_prefix", String(16), nullable=False),
    Column("key_hash", String(64), nullable=False, unique=True),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True)),
)
searches = Table(
    "searches",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("status", String(24), nullable=False),
    Column("options_json", Text, nullable=False),
    Column("idempotency_key", String(128)),
    Column("error", Text, nullable=False, default=""),
    Column("warnings_json", Text, nullable=False, default="[]"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("lease_owner", String(100)),
    Column("lease_until", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("customer_id", "idempotency_key", name="search_customer_idempotency_uq"),
)
conferences = Table(
    "conferences",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("search_id", ForeignKey("searches.id", ondelete="CASCADE"), nullable=False),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("rank", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("search_id", "rank", name="conference_search_rank_uq"),
)
enrichment_jobs = Table(
    "enrichment_jobs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("search_id", ForeignKey("searches.id", ondelete="CASCADE"), nullable=False),
    Column("result_id", ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
    Column("mode", String(24), nullable=False),
    Column("status", String(24), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("lease_owner", String(100)),
    Column("lease_until", DateTime(timezone=True)),
    Column("error", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
provider_attempts = Table(
    "provider_attempts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("search_id", ForeignKey("searches.id", ondelete="CASCADE"), nullable=False),
    Column("result_id", ForeignKey("conferences.id", ondelete="CASCADE"), nullable=False),
    Column(
        "enrichment_job_id", ForeignKey("enrichment_jobs.id", ondelete="CASCADE"), nullable=False
    ),
    Column("provider", String(64), nullable=False),
    Column("strategy", String(64), nullable=False),
    Column("status", String(24), nullable=False),
    Column("duration_ms", Integer, nullable=False, default=0),
    Column("billable_units", Integer, nullable=False, default=0),
    Column("evidence_json", Text, nullable=False, default="[]"),
    Column("response_json", Text, nullable=False, default="{}"),
    Column("error", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
usage_events = Table(
    "usage_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("quantity", Integer, nullable=False, default=1),
    Column("metadata_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
provider_slots = Table(
    "provider_slots",
    metadata,
    Column("provider", String(64), primary_key=True),
    Column("slot_number", Integer, primary_key=True),
    Column("lease_owner", String(100)),
    Column("lease_until", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("searches_customer_created_idx", searches.c.customer_id, searches.c.created_at)
Index("searches_claim_idx", searches.c.status, searches.c.lease_until)
Index("conferences_customer_search_idx", conferences.c.customer_id, conferences.c.search_id)
Index("enrichment_claim_idx", enrichment_jobs.c.status, enrichment_jobs.c.lease_until)
Index("attempts_result_idx", provider_attempts.c.customer_id, provider_attempts.c.result_id)
Index("usage_customer_created_idx", usage_events.c.customer_id, usage_events.c.created_at)


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


class SearchStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        database_url: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings()
        if database_url:
            url = database_url
        elif path is not None:
            normalized = str(Path(path).expanduser().resolve()).replace("\\", "/")
            url = f"sqlite:///{normalized}"
        else:
            url = self.settings.storage_url
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        elif self.engine.dialect.name == "postgresql":
            event.listen(
                self.engine,
                "connect",
                partial(
                    self._set_postgres_schema,
                    schema=self.settings.validated_database_schema,
                ),
            )
        legacy_schema = self._prepare_legacy_sqlite()
        if self.settings.auto_create_schema:
            metadata.create_all(self.engine)
        self.ensure_customer(self.settings.bootstrap_customer_id, "Local customer")
        self.ensure_provider_slots(
            "contact_finder", max(1, self.settings.contact_finder_max_concurrent)
        )
        if legacy_schema:
            self._copy_legacy_sqlite()
        if self.settings.bootstrap_api_key:
            self.create_api_key(
                self.settings.bootstrap_customer_id,
                self.settings.bootstrap_api_key,
                name="bootstrap",
            )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @staticmethod
    def _set_postgres_schema(dbapi_connection: Any, _: Any, *, schema: str) -> None:
        previous_autocommit = dbapi_connection.autocommit
        try:
            dbapi_connection.autocommit = True
            cursor = dbapi_connection.cursor()
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cursor.execute(f'SET search_path TO "{schema}", public')
            cursor.close()
        finally:
            dbapi_connection.autocommit = previous_autocommit

    def _prepare_legacy_sqlite(self) -> bool:
        """Move the v0.1 payload tables aside so their rows can be preserved."""
        if self.engine.dialect.name != "sqlite":
            return False
        inspector = inspect(self.engine)
        if "searches" not in inspector.get_table_names():
            return False
        columns = {column["name"] for column in inspector.get_columns("searches")}
        if "customer_id" in columns:
            return False
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE searches RENAME TO legacy_searches_v01"))
            if "conferences" in inspector.get_table_names():
                connection.execute(text("ALTER TABLE conferences RENAME TO legacy_conferences_v01"))
        return True

    def _copy_legacy_sqlite(self) -> None:
        now = _now()
        customer_id = self.settings.bootstrap_customer_id
        with self.engine.begin() as connection:
            legacy_searches = connection.execute(
                text(
                    "SELECT id, status, options_json, error, created_at, updated_at "
                    "FROM legacy_searches_v01"
                )
            ).mappings()
            for row in legacy_searches:
                status = "queued" if row["status"] in {"queued", "running"} else row["status"]
                connection.execute(
                    insert(searches).values(
                        id=row["id"],
                        customer_id=customer_id,
                        status=status,
                        options_json=row["options_json"],
                        error=row["error"] or "",
                        warnings_json="[]",
                        attempts=0,
                        max_attempts=3,
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                    )
                )
            legacy_results = connection.execute(
                text("SELECT id, search_id, rank, payload_json FROM legacy_conferences_v01")
            ).mappings()
            for row in legacy_results:
                connection.execute(
                    insert(conferences).values(
                        id=row["id"],
                        search_id=row["search_id"],
                        customer_id=customer_id,
                        rank=row["rank"],
                        payload_json=row["payload_json"],
                        created_at=now,
                        updated_at=now,
                    )
                )

    def ensure_customer(self, customer_id: str, name: str = "Customer") -> None:
        try:
            with self.engine.begin() as connection:
                if connection.execute(
                    select(customers.c.id).where(customers.c.id == customer_id)
                ).first():
                    return
                connection.execute(
                    insert(customers).values(
                        id=customer_id, name=name, active=True, created_at=_now()
                    )
                )
        except IntegrityError:
            with self.engine.connect() as connection:
                if not connection.execute(
                    select(customers.c.id).where(customers.c.id == customer_id)
                ).first():
                    raise

    def ensure_provider_slots(self, provider: str, count: int) -> None:
        for slot_number in range(1, max(1, count) + 1):
            try:
                with self.engine.begin() as connection:
                    if connection.execute(
                        select(provider_slots.c.slot_number).where(
                            provider_slots.c.provider == provider,
                            provider_slots.c.slot_number == slot_number,
                        )
                    ).first():
                        continue
                    connection.execute(
                        insert(provider_slots).values(
                            provider=provider, slot_number=slot_number, updated_at=_now()
                        )
                    )
            except IntegrityError:
                pass

    def acquire_provider_slot(
        self, provider: str, worker_id: str, lease_seconds: int
    ) -> int | None:
        now = _now()
        candidate = (
            select(provider_slots.c.slot_number)
            .where(
                provider_slots.c.provider == provider,
                or_(
                    provider_slots.c.lease_until.is_(None),
                    provider_slots.c.lease_until < now,
                ),
            )
            .order_by(provider_slots.c.slot_number)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        with self.engine.begin() as connection:
            row = connection.execute(
                update(provider_slots)
                .where(
                    provider_slots.c.provider == provider,
                    provider_slots.c.slot_number == candidate,
                )
                .values(
                    lease_owner=worker_id,
                    lease_until=now + timedelta(seconds=max(30, lease_seconds)),
                    updated_at=now,
                )
                .returning(provider_slots.c.slot_number)
            ).first()
        return int(row.slot_number) if row else None

    def release_provider_slot(self, provider: str, slot_number: int, worker_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(provider_slots)
                .where(
                    provider_slots.c.provider == provider,
                    provider_slots.c.slot_number == slot_number,
                    provider_slots.c.lease_owner == worker_id,
                )
                .values(lease_owner=None, lease_until=None, updated_at=_now())
            )

    def defer_enrichment(self, job_id: str, customer_id: str, worker_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(enrichment_jobs)
                .where(
                    enrichment_jobs.c.id == job_id,
                    enrichment_jobs.c.customer_id == customer_id,
                    enrichment_jobs.c.status == "running",
                    enrichment_jobs.c.lease_owner == worker_id,
                )
                .values(
                    status="queued",
                    attempts=case(
                        (enrichment_jobs.c.attempts > 0, enrichment_jobs.c.attempts - 1),
                        else_=0,
                    ),
                    lease_owner=None,
                    lease_until=None,
                    updated_at=_now(),
                )
            )

    @staticmethod
    def _lock_customer(connection: Any, customer_id: str) -> None:
        """Serialize tenant-scoped reservations on databases that support row locks."""
        row = connection.execute(
            select(customers.c.id)
            .where(customers.c.id == customer_id, customers.c.active.is_(True))
            .with_for_update()
        ).first()
        if row is None:
            raise PermissionError("Customer is inactive or unavailable")

    @staticmethod
    def _usage_in_transaction(connection: Any, customer_id: str, kind: str, since: datetime) -> int:
        value = connection.execute(
            select(func.coalesce(func.sum(usage_events.c.quantity), 0)).where(
                usage_events.c.customer_id == customer_id,
                usage_events.c.kind == kind,
                usage_events.c.created_at >= since,
            )
        ).scalar_one()
        return int(value)

    def create_api_key(self, customer_id: str, raw_key: str, name: str = "default") -> str:
        if len(raw_key) < 20:
            raise ValueError("API keys must contain at least 20 characters")
        self.ensure_customer(customer_id)
        digest = hash_api_key(raw_key, self.settings.api_key_pepper)
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(api_keys.c.id).where(api_keys.c.key_hash == digest)
                ).first()
                if existing:
                    return str(existing.id)
                key_id = str(uuid.uuid4())
                connection.execute(
                    insert(api_keys).values(
                        id=key_id,
                        customer_id=customer_id,
                        name=name,
                        key_prefix=raw_key[:8],
                        key_hash=digest,
                        active=True,
                        created_at=_now(),
                    )
                )
                return key_id
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = connection.execute(
                    select(api_keys.c.id).where(api_keys.c.key_hash == digest)
                ).first()
                if existing:
                    return str(existing.id)
            raise

    def verify_api_key(self, raw_key: str) -> str | None:
        if not raw_key:
            return None
        digest = hash_api_key(raw_key, self.settings.api_key_pepper)
        with self.engine.begin() as connection:
            row = connection.execute(
                select(api_keys.c.id, api_keys.c.customer_id)
                .join(customers, customers.c.id == api_keys.c.customer_id)
                .where(
                    api_keys.c.key_hash == digest,
                    api_keys.c.active.is_(True),
                    customers.c.active.is_(True),
                )
            ).first()
            if row is None:
                return None
            connection.execute(
                update(api_keys).where(api_keys.c.id == row.id).values(last_used_at=_now())
            )
            return str(row.customer_id)

    def revoke_api_key(self, key_id: str, customer_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(api_keys)
                .where(api_keys.c.id == key_id, api_keys.c.customer_id == customer_id)
                .values(active=False)
            )
            return result.rowcount > 0

    def count_usage_since(self, customer_id: str, kind: str, since: datetime) -> int:
        with self.engine.connect() as connection:
            value = connection.execute(
                select(func.coalesce(func.sum(usage_events.c.quantity), 0)).where(
                    usage_events.c.customer_id == customer_id,
                    usage_events.c.kind == kind,
                    usage_events.c.created_at >= since,
                )
            ).scalar_one()
        return int(value)

    def record_usage(
        self, customer_id: str, kind: str, quantity: int = 1, metadata_value: dict | None = None
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    customer_id=customer_id,
                    kind=kind,
                    quantity=max(0, quantity),
                    metadata_json=_json(metadata_value or {}),
                    created_at=_now(),
                )
            )

    def create_search(
        self, options: SearchOptions, customer_id: str = "local", idempotency_key: str | None = None
    ) -> str:
        self.ensure_customer(customer_id)
        normalized_key = (idempotency_key or "").strip()[:128] or None
        now = _now()
        with self.engine.begin() as connection:
            self._lock_customer(connection, customer_id)
            if normalized_key:
                existing = connection.execute(
                    select(searches.c.id, searches.c.options_json).where(
                        searches.c.customer_id == customer_id,
                        searches.c.idempotency_key == normalized_key,
                    )
                ).first()
                if existing:
                    if json.loads(existing.options_json) != options.as_dict():
                        raise ValueError(
                            "Idempotency key was already used with different search options"
                        )
                    return str(existing.id)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if (
                self._usage_in_transaction(
                    connection, customer_id, "conference_search", start_of_day
                )
                >= self.settings.search_daily_limit
            ):
                raise PermissionError("Daily conference search quota reached")
            search_id = str(uuid.uuid4())
            connection.execute(
                insert(searches).values(
                    id=search_id,
                    customer_id=customer_id,
                    status="queued",
                    options_json=_json(options.as_dict()),
                    idempotency_key=normalized_key,
                    error="",
                    warnings_json="[]",
                    attempts=0,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    customer_id=customer_id,
                    kind="conference_search",
                    quantity=1,
                    metadata_json=_json({"search_id": search_id}),
                    created_at=now,
                )
            )
            return search_id

    def claim_search(self, worker_id: str, lease_seconds: int = 600) -> dict[str, Any] | None:
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                update(searches)
                .where(
                    searches.c.status == "running",
                    searches.c.lease_until < now,
                    searches.c.attempts >= searches.c.max_attempts,
                )
                .values(
                    status="error",
                    error="Worker lease expired after maximum attempts",
                    lease_owner=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
        candidate = (
            select(searches.c.id)
            .where(
                searches.c.attempts < searches.c.max_attempts,
                or_(
                    searches.c.status == "queued",
                    and_(searches.c.status == "running", searches.c.lease_until < now),
                ),
            )
            .order_by(searches.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(searches)
            .where(searches.c.id == candidate)
            .values(
                status="running",
                attempts=searches.c.attempts + 1,
                lease_owner=worker_id,
                lease_until=now + timedelta(seconds=max(30, lease_seconds)),
                updated_at=now,
            )
            .returning(searches.c.id, searches.c.customer_id, searches.c.options_json)
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return {
            "search_id": row.id,
            "customer_id": row.customer_id,
            "options": json.loads(row.options_json),
        }

    def complete(
        self,
        search_id: str,
        conferences_value: list[Conference],
        customer_id: str = "local",
        warnings: list[str] | None = None,
        auto_enrichment_mode: str = "none",
        max_enrichments: int = 0,
        worker_id: str | None = None,
    ) -> list[str]:
        now = _now()
        result_ids: list[str] = []
        with self.engine.begin() as connection:
            ownership = [searches.c.id == search_id, searches.c.customer_id == customer_id]
            if worker_id:
                ownership.append(searches.c.lease_owner == worker_id)
            if (
                connection.execute(
                    select(searches.c.id).where(*ownership).with_for_update()
                ).first()
                is None
            ):
                return []
            self._lock_customer(connection, customer_id)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            enrichment_remaining = max(
                0,
                self.settings.enrichment_daily_limit
                - self._usage_in_transaction(
                    connection, customer_id, "contact_enrichment", start_of_day
                ),
            )
            connection.execute(
                delete(conferences).where(
                    conferences.c.search_id == search_id, conferences.c.customer_id == customer_id
                )
            )
            for rank, conference in enumerate(conferences_value, start=1):
                result_id = conference.id or str(uuid.uuid4())
                conference.id = result_id
                result_ids.append(result_id)
                connection.execute(
                    insert(conferences).values(
                        id=result_id,
                        search_id=search_id,
                        customer_id=customer_id,
                        rank=rank,
                        payload_json=_json(conference.as_dict()),
                        created_at=now,
                        updated_at=now,
                    )
                )
                if (
                    auto_enrichment_mode in {"basic", "standard", "deep"}
                    and rank <= max_enrichments
                    and enrichment_remaining > 0
                    and (not conference.contact_name or not conference.contact_email)
                ):
                    job_id = str(uuid.uuid4())
                    connection.execute(
                        insert(enrichment_jobs).values(
                            id=job_id,
                            customer_id=customer_id,
                            search_id=search_id,
                            result_id=result_id,
                            mode=auto_enrichment_mode,
                            status="queued",
                            attempts=0,
                            max_attempts=3,
                            error="",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    enrichment_remaining -= 1
                    connection.execute(
                        insert(usage_events).values(
                            id=str(uuid.uuid4()),
                            customer_id=customer_id,
                            kind="contact_enrichment",
                            quantity=1,
                            metadata_json=_json({"job_id": job_id, "result_id": result_id}),
                            created_at=now,
                        )
                    )
            connection.execute(
                update(searches)
                .where(*ownership)
                .values(
                    status="completed",
                    error="",
                    warnings_json=_json(warnings or []),
                    lease_owner=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
        return result_ids

    def fail_search(
        self, search_id: str, customer_id: str, error: str, worker_id: str | None = None
    ) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(searches.c.attempts, searches.c.max_attempts).where(
                    searches.c.id == search_id, searches.c.customer_id == customer_id
                )
            ).first()
            if row is None:
                return
            next_status = "queued" if row.attempts < row.max_attempts else "error"
            conditions = [searches.c.id == search_id, searches.c.customer_id == customer_id]
            if worker_id:
                conditions.append(searches.c.lease_owner == worker_id)
            connection.execute(
                update(searches)
                .where(*conditions)
                .values(
                    status=next_status,
                    error=str(error)[:500],
                    lease_owner=None,
                    lease_until=None,
                    updated_at=_now(),
                )
            )

    def set_running(self, search_id: str, customer_id: str = "local") -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(searches)
                .where(searches.c.id == search_id, searches.c.customer_id == customer_id)
                .values(status="running", updated_at=_now())
            )

    def fail(self, search_id: str, error: str, customer_id: str = "local") -> None:
        self.fail_search(search_id, customer_id, error)

    def get_search(self, search_id: str, customer_id: str = "local") -> dict[str, Any] | None:
        count_query = (
            select(func.count(conferences.c.id))
            .where(conferences.c.search_id == searches.c.id)
            .scalar_subquery()
        )
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(searches, count_query.label("results_count")).where(
                        searches.c.id == search_id, searches.c.customer_id == customer_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return {
            "search_id": row["id"],
            "status": row["status"],
            "options": json.loads(row["options_json"]),
            "results_count": row["results_count"],
            "error": row["error"] or None,
            "warnings": json.loads(row["warnings_json"]),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def get_results(
        self, search_id: str, limit: int = 100, offset: int = 0, customer_id: str = "local"
    ) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(conferences.c.payload_json)
                .where(
                    conferences.c.search_id == search_id, conferences.c.customer_id == customer_id
                )
                .order_by(conferences.c.rank)
                .limit(max(1, min(limit, 100)))
                .offset(max(0, offset))
            ).all()
        return [json.loads(row.payload_json) for row in rows]

    def get_conference(self, result_id: str, customer_id: str = "local") -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(conferences.c.search_id, conferences.c.payload_json).where(
                    conferences.c.id == result_id, conferences.c.customer_id == customer_id
                )
            ).first()
        if row is None:
            return None
        payload = json.loads(row.payload_json)
        payload["search_id"] = row.search_id
        payload["enrichment"] = self.latest_enrichment(result_id, customer_id)
        return payload

    def update_conference_contact(
        self,
        result_id: str,
        customer_id: str,
        patch: dict[str, Any],
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            if job_id and worker_id:
                owned_job = connection.execute(
                    select(enrichment_jobs.c.id).where(
                        enrichment_jobs.c.id == job_id,
                        enrichment_jobs.c.customer_id == customer_id,
                        enrichment_jobs.c.result_id == result_id,
                        enrichment_jobs.c.status == "running",
                        enrichment_jobs.c.lease_owner == worker_id,
                    )
                ).first()
                if owned_job is None:
                    return None
            row = connection.execute(
                select(conferences.c.payload_json).where(
                    conferences.c.id == result_id, conferences.c.customer_id == customer_id
                )
            ).first()
            if row is None:
                return None
            payload = json.loads(row.payload_json)
            for field in ("contact_name", "contact_role", "contact_email", "contact_linkedin"):
                if not payload.get(field) and patch.get(field):
                    payload[field] = patch[field]
            if patch.get("provider") and any(
                patch.get(k) for k in ("contact_name", "contact_email", "contact_linkedin")
            ):
                payload["contact_source"] = payload.get("contact_source") or patch["provider"]
                payload["contact_confidence"] = max(
                    float(payload.get("contact_confidence") or 0),
                    float(patch.get("confidence") or 0),
                )
            evidence = list(payload.get("evidence") or [])
            for value in patch.get("source_urls") or []:
                if value and value not in evidence:
                    evidence.append(value)
            payload["evidence"] = evidence
            connection.execute(
                update(conferences)
                .where(conferences.c.id == result_id, conferences.c.customer_id == customer_id)
                .values(payload_json=_json(payload), updated_at=_now())
            )
            return payload

    def enqueue_enrichment(self, result_id: str, customer_id: str, mode: str = "standard") -> str:
        if mode not in {"basic", "standard", "deep", "tier3"}:
            raise ValueError("Unsupported enrichment mode")
        now = _now()
        with self.engine.begin() as connection:
            self._lock_customer(connection, customer_id)
            row = connection.execute(
                select(conferences.c.search_id).where(
                    conferences.c.id == result_id, conferences.c.customer_id == customer_id
                )
            ).first()
            if row is None:
                raise ValueError("Conference result not found")
            existing = connection.execute(
                select(enrichment_jobs.c.id).where(
                    enrichment_jobs.c.result_id == result_id,
                    enrichment_jobs.c.customer_id == customer_id,
                    enrichment_jobs.c.status.in_(("queued", "running")),
                )
            ).first()
            if existing:
                return str(existing.id)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if (
                self._usage_in_transaction(
                    connection, customer_id, "contact_enrichment", start_of_day
                )
                >= self.settings.enrichment_daily_limit
            ):
                raise PermissionError("Daily contact enrichment quota reached")
            job_id = str(uuid.uuid4())
            connection.execute(
                insert(enrichment_jobs).values(
                    id=job_id,
                    customer_id=customer_id,
                    search_id=row.search_id,
                    result_id=result_id,
                    mode=mode,
                    status="queued",
                    attempts=0,
                    max_attempts=3,
                    error="",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    customer_id=customer_id,
                    kind="contact_enrichment",
                    quantity=1,
                    metadata_json=_json({"job_id": job_id, "result_id": result_id}),
                    created_at=now,
                )
            )
            return job_id

    def claim_enrichment(self, worker_id: str, lease_seconds: int = 600) -> dict[str, Any] | None:
        now = _now()
        with self.engine.begin() as connection:
            connection.execute(
                update(enrichment_jobs)
                .where(
                    enrichment_jobs.c.status == "running",
                    enrichment_jobs.c.lease_until < now,
                    enrichment_jobs.c.attempts >= enrichment_jobs.c.max_attempts,
                )
                .values(
                    status="failed",
                    error="Worker lease expired after maximum attempts",
                    lease_owner=None,
                    lease_until=None,
                    updated_at=now,
                )
            )
        candidate = (
            select(enrichment_jobs.c.id)
            .where(
                enrichment_jobs.c.attempts < enrichment_jobs.c.max_attempts,
                or_(
                    enrichment_jobs.c.status == "queued",
                    and_(
                        enrichment_jobs.c.status == "running", enrichment_jobs.c.lease_until < now
                    ),
                ),
            )
            .order_by(enrichment_jobs.c.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(enrichment_jobs)
            .where(enrichment_jobs.c.id == candidate)
            .values(
                status="running",
                attempts=enrichment_jobs.c.attempts + 1,
                lease_owner=worker_id,
                lease_until=now + timedelta(seconds=max(30, lease_seconds)),
                updated_at=now,
            )
            .returning(
                enrichment_jobs.c.id,
                enrichment_jobs.c.customer_id,
                enrichment_jobs.c.search_id,
                enrichment_jobs.c.result_id,
                enrichment_jobs.c.mode,
            )
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).first()
        return dict(row._mapping) if row else None

    def finish_enrichment(
        self,
        job_id: str,
        customer_id: str,
        worker_id: str | None = None,
        contact_found: bool = True,
    ) -> None:
        with self.engine.begin() as connection:
            conditions = [
                enrichment_jobs.c.id == job_id,
                enrichment_jobs.c.customer_id == customer_id,
            ]
            if worker_id:
                conditions.append(enrichment_jobs.c.lease_owner == worker_id)
            connection.execute(
                update(enrichment_jobs)
                .where(*conditions)
                .values(
                    status="completed" if contact_found else "no_contact",
                    error="",
                    lease_owner=None,
                    lease_until=None,
                    updated_at=_now(),
                )
            )

    def fail_enrichment(
        self, job_id: str, customer_id: str, error: str, worker_id: str | None = None
    ) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(enrichment_jobs.c.attempts, enrichment_jobs.c.max_attempts).where(
                    enrichment_jobs.c.id == job_id, enrichment_jobs.c.customer_id == customer_id
                )
            ).first()
            if row is None:
                return
            status = "queued" if row.attempts < row.max_attempts else "failed"
            conditions = [
                enrichment_jobs.c.id == job_id,
                enrichment_jobs.c.customer_id == customer_id,
            ]
            if worker_id:
                conditions.append(enrichment_jobs.c.lease_owner == worker_id)
            connection.execute(
                update(enrichment_jobs)
                .where(*conditions)
                .values(
                    status=status,
                    error=str(error)[:500],
                    lease_owner=None,
                    lease_until=None,
                    updated_at=_now(),
                )
            )

    def latest_enrichment(self, result_id: str, customer_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(enrichment_jobs)
                    .where(
                        enrichment_jobs.c.result_id == result_id,
                        enrichment_jobs.c.customer_id == customer_id,
                    )
                    .order_by(enrichment_jobs.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        payload = self._enrichment_payload(row)
        payload["provider_attempts"] = self.get_provider_attempts(row["id"], customer_id)
        return payload

    def get_enrichment(self, job_id: str, customer_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(enrichment_jobs).where(
                        enrichment_jobs.c.id == job_id, enrichment_jobs.c.customer_id == customer_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        payload = self._enrichment_payload(row)
        payload["provider_attempts"] = self.get_provider_attempts(row["id"], customer_id)
        return payload

    def get_provider_attempts(self, job_id: str, customer_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        provider_attempts.c.provider,
                        provider_attempts.c.strategy,
                        provider_attempts.c.status,
                        provider_attempts.c.duration_ms,
                        provider_attempts.c.billable_units,
                        provider_attempts.c.evidence_json,
                        provider_attempts.c.error,
                        provider_attempts.c.created_at,
                    )
                    .where(
                        provider_attempts.c.enrichment_job_id == job_id,
                        provider_attempts.c.customer_id == customer_id,
                    )
                    .order_by(provider_attempts.c.created_at)
                )
                .mappings()
                .all()
            )
        return [
            {
                "provider": row["provider"],
                "strategy": row["strategy"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "billable_units": row["billable_units"],
                "evidence": json.loads(row["evidence_json"]),
                "error": row["error"] or None,
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]

    @staticmethod
    def _enrichment_payload(row: Any) -> dict[str, Any]:
        return {
            "job_id": row["id"],
            "result_id": row["result_id"],
            "status": row["status"],
            "mode": row["mode"],
            "attempts": row["attempts"],
            "error": row["error"] or None,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def record_provider_attempt(
        self,
        job: dict[str, Any],
        provider: str,
        strategy: str,
        status: str,
        duration_ms: int,
        evidence: list[str] | None = None,
        response: dict[str, Any] | None = None,
        error: str = "",
        billable_units: int = 0,
        worker_id: str | None = None,
    ) -> bool:
        with self.engine.begin() as connection:
            if worker_id:
                owned_job = connection.execute(
                    select(enrichment_jobs.c.id).where(
                        enrichment_jobs.c.id == job["id"],
                        enrichment_jobs.c.customer_id == job["customer_id"],
                        enrichment_jobs.c.status == "running",
                        enrichment_jobs.c.lease_owner == worker_id,
                    )
                ).first()
                if owned_job is None:
                    return False
            connection.execute(
                insert(provider_attempts).values(
                    id=str(uuid.uuid4()),
                    customer_id=job["customer_id"],
                    search_id=job["search_id"],
                    result_id=job["result_id"],
                    enrichment_job_id=job["id"],
                    provider=provider,
                    strategy=strategy,
                    status=status,
                    duration_ms=max(0, duration_ms),
                    billable_units=max(0, billable_units),
                    evidence_json=_json(evidence or []),
                    response_json=_json(response or {}),
                    error=str(error)[:500],
                    created_at=_now(),
                )
            )
            return True

    def delete_search(self, search_id: str, customer_id: str = "local") -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(searches).where(
                    searches.c.id == search_id, searches.c.customer_id == customer_id
                )
            )
            return result.rowcount > 0
