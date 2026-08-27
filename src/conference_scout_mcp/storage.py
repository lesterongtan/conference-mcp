from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Conference, SearchOptions


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SearchStore:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("CONFERENCE_SCOUT_DB", "conference-scout.db")
        self.path = Path(configured).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS searches (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conferences (
                    id TEXT PRIMARY KEY,
                    search_id TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    rank INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conferences_search_id_idx
                    ON conferences(search_id, rank);
                """
            )

    def create_search(self, options: SearchOptions) -> str:
        search_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO searches VALUES (?, 'queued', ?, '', ?, ?)",
                (search_id, json.dumps(options.as_dict()), now, now),
            )
        return search_id

    def set_running(self, search_id: str) -> None:
        self._set_status(search_id, "running")

    def fail(self, search_id: str, error: str) -> None:
        self._set_status(search_id, "error", error[:2000])

    def complete(self, search_id: str, conferences: list[Conference]) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("DELETE FROM conferences WHERE search_id = ?", (search_id,))
            for rank, conference in enumerate(conferences, start=1):
                result_id = conference.id or str(uuid.uuid4())
                conference.id = result_id
                connection.execute(
                    """
                    INSERT INTO conferences(id, search_id, rank, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (result_id, search_id, rank, json.dumps(conference.as_dict())),
                )
            connection.execute(
                "UPDATE searches SET status = 'completed', error = '', updated_at = ? WHERE id = ?",
                (now, search_id),
            )

    def _set_status(self, search_id: str, status: str, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE searches SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, _now(), search_id),
            )

    def get_search(self, search_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, COUNT(c.id) AS results_count
                FROM searches s LEFT JOIN conferences c ON c.search_id = s.id
                WHERE s.id = ? GROUP BY s.id
                """,
                (search_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "search_id": row["id"],
            "status": row["status"],
            "options": json.loads(row["options_json"]),
            "results_count": row["results_count"],
            "error": row["error"] or None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_results(self, search_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM conferences
                WHERE search_id = ? ORDER BY rank LIMIT ? OFFSET ?
                """,
                (search_id, max(1, min(limit, 100)), max(0, offset)),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def get_conference(self, result_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT search_id, payload_json FROM conferences WHERE id = ?", (result_id,)
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["search_id"] = row["search_id"]
        return payload

    def delete_search(self, search_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        return cursor.rowcount > 0
