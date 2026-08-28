from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("CONFERENCE_SCOUT_DATABASE_URL", "")
    auto_create_schema: bool = _bool("CONFERENCE_SCOUT_AUTO_CREATE_SCHEMA", True)
    sqlite_path: str = os.getenv("CONFERENCE_SCOUT_DB", "conference-scout.db")
    require_auth: bool = _bool("CONFERENCE_SCOUT_REQUIRE_AUTH")
    public_url: str = os.getenv("CONFERENCE_SCOUT_PUBLIC_URL", "http://127.0.0.1:8765")
    http_host: str = os.getenv("CONFERENCE_SCOUT_HTTP_HOST", "127.0.0.1")
    cors_origins: str = os.getenv("CONFERENCE_SCOUT_CORS_ORIGINS", "")
    embedded_worker: bool = _bool("CONFERENCE_SCOUT_EMBEDDED_WORKER", True)
    bootstrap_customer_id: str = os.getenv("CONFERENCE_SCOUT_BOOTSTRAP_CUSTOMER_ID", "local")
    bootstrap_api_key: str = os.getenv("CONFERENCE_SCOUT_BOOTSTRAP_API_KEY", "")
    api_key_pepper: str = os.getenv("CONFERENCE_SCOUT_API_KEY_PEPPER", "")
    worker_poll_seconds: float = float(os.getenv("CONFERENCE_SCOUT_WORKER_POLL_SECONDS", "1"))
    worker_lease_seconds: int = int(os.getenv("CONFERENCE_SCOUT_WORKER_LEASE_SECONDS", "1800"))
    search_daily_limit: int = int(os.getenv("CONFERENCE_SCOUT_SEARCH_DAILY_LIMIT", "100"))
    enrichment_daily_limit: int = int(os.getenv("CONFERENCE_SCOUT_ENRICHMENT_DAILY_LIMIT", "100"))
    max_response_bytes: int = int(os.getenv("CONFERENCE_SCOUT_MAX_RESPONSE_BYTES", "2000000"))
    discovery_max_pages: int = int(os.getenv("CONFERENCE_SCOUT_DISCOVERY_MAX_PAGES", "40"))
    discovery_max_concurrent: int = int(os.getenv("CONFERENCE_SCOUT_DISCOVERY_MAX_CONCURRENT", "8"))
    contact_finder_url: str = os.getenv("CONTACT_FINDER_URL", "")
    contact_finder_token: str = os.getenv("CONTACT_FINDER_TOKEN", "")
    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    firecrawl_max_pages: int = int(os.getenv("FIRECRAWL_MAX_PAGES", "3"))
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    contact_finder_search_first_enabled: bool = _bool("CONTACT_FINDER_SEARCH_FIRST_ENABLED", True)
    contact_finder_poll_seconds: float = float(os.getenv("CONTACT_FINDER_POLL_SECONDS", "4"))
    contact_finder_timeout_seconds: float = float(
        os.getenv("CONTACT_FINDER_TIMEOUT_SECONDS", "400")
    )
    contact_finder_search_first_timeout_seconds: float = float(
        os.getenv("CONTACT_FINDER_SEARCH_FIRST_TIMEOUT_SECONDS", "150")
    )
    contact_finder_max_concurrent: int = int(os.getenv("CONTACT_FINDER_MAX_CONCURRENT", "2"))
    external_super_browser_url: str = os.getenv("EXTERNAL_SUPER_BROWSER_MCP_URL", "")
    external_super_browser_token: str = os.getenv("EXTERNAL_SUPER_BROWSER_TOKEN", "")

    @property
    def storage_url(self) -> str:
        if self.database_url:
            return self.database_url
        normalized = self.sqlite_path.replace("\\", "/")
        return f"sqlite:///{normalized}"
