from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from .config import Settings
from .models import Conference, SearchOptions
from .providers import (
    ApifyDirectoryProvider,
    ConfsTechProvider,
    DiscoveryProvider,
    SerperProvider,
)

logger = logging.getLogger(__name__)

STOP_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"}
TOKEN_CONCEPTS = {
    "ai": "ai",
    "ml": "ai",
    "health": "healthcare",
    "healthcare": "healthcare",
    "medical": "healthcare",
    "medicine": "healthcare",
    "clinical": "healthcare",
}
US_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
}
US_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
US_ALIASES = {"united states", "united states of america", "usa", "u.s.a.", "us", "u.s."}


def _concept_tokens(value: str) -> set[str]:
    normalized = value.casefold()
    normalized = re.sub(
        r"\b(?:artificial intelligence|machine learning|generative ai)\b", " ai ", normalized
    )
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in STOP_WORDS
    }
    return {TOKEN_CONCEPTS.get(token, token) for token in tokens}


def _matched_keywords(conference: Conference, options: SearchOptions) -> list[str]:
    haystack = " ".join(
        (conference.name, conference.description, conference.location)
    ).casefold()
    concepts = _concept_tokens(haystack)
    matched: list[str] = []
    for keyword in options.keywords:
        phrase = keyword.casefold()
        required = _concept_tokens(keyword)
        if phrase in haystack or (required and required.issubset(concepts)):
            matched.append(keyword)
    return matched


def _matches_geography(location: str, geography: str) -> bool:
    if not geography:
        return True
    normalized_location = " ".join(location.casefold().split())
    normalized_geography = " ".join(geography.casefold().split())
    if not normalized_location:
        return False
    if normalized_geography in US_ALIASES:
        if re.search(
            r"(?:\bunited states(?: of america)?\b|\busa\b|\bu\.s(?:\.a)?\.(?=\W|$)|\bus\b)",
            normalized_location,
        ):
            return True
        if any(name in normalized_location for name in US_NAMES):
            return True
        return any(re.search(rf"\b{code}\b", location) for code in US_CODES)
    return normalized_geography in normalized_location


def _identity(conference: Conference) -> str:
    parsed = urlparse(conference.url or conference.submission_url)
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/").casefold()
    if host:
        return f"{host}{path}"
    return re.sub(r"\W+", " ", conference.name.casefold()).strip()


def _score(conference: Conference, options: SearchOptions) -> float:
    conference.matched_keywords = _matched_keywords(conference, options)
    score = len(conference.matched_keywords) * 20.0
    if conference.cfp_status == "Open":
        score += 25
    elif conference.cfp_status == "Closed":
        score -= 10
    if conference.submission_url:
        score += 15
    if conference.event_date:
        score += 10
    if conference.contact_email:
        score += 5
    if options.geography and options.geography.casefold() in conference.location.casefold():
        score += 15
    if conference.source == "confs.tech":
        score += 5
    conference.score = score
    return score


class DiscoveryEngine:
    def __init__(
        self,
        providers: list[DiscoveryProvider] | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings()
        self.providers = providers or [
            ConfsTechProvider(),
            SerperProvider(settings.serper_api_key),
            ApifyDirectoryProvider(settings),
        ]
        self.last_diagnostics: list[dict[str, object]] = []

    def diagnostic_warnings(self) -> list[str]:
        return [
            "provider:{provider}:{status}:results={results}{error}".format(
                provider=item["provider"],
                status=item["status"],
                results=item.get("results", 0),
                error=f":{item['error']}" if item.get("error") else "",
            )
            for item in self.last_diagnostics
        ]

    def search(self, options: SearchOptions) -> list[Conference]:
        discovered: list[Conference] = []
        self.last_diagnostics = []
        configured: list[DiscoveryProvider] = []
        for provider in self.providers:
            if not getattr(provider, "configured", True):
                self.last_diagnostics.append(
                    {"provider": provider.name, "status": "skipped", "results": 0}
                )
            else:
                configured.append(provider)
        with ThreadPoolExecutor(max_workers=max(1, len(configured))) as executor:
            futures = {
                executor.submit(provider.discover, options): provider for provider in configured
            }
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    values = future.result()
                    discovered.extend(values)
                    self.last_diagnostics.append(
                        {"provider": provider.name, "status": "completed", "results": len(values)}
                    )
                except Exception as exc:
                    logger.exception("Discovery provider failed: %s", provider.name)
                    self.last_diagnostics.append(
                        {
                            "provider": provider.name,
                            "status": "failed",
                            "results": 0,
                            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                        }
                    )

        unique: dict[str, Conference] = {}
        excluded_relevance = 0
        excluded_geography = 0
        for conference in discovered:
            if not conference.name or not (conference.url or conference.submission_url):
                continue
            if not conference.date_in_window(options):
                continue
            if options.open_cfp_only and conference.cfp_status != "Open":
                continue
            if not _matched_keywords(conference, options):
                excluded_relevance += 1
                continue
            if not _matches_geography(conference.location, options.geography):
                excluded_geography += 1
                continue
            key = _identity(conference)
            existing = unique.get(key)
            if existing is None or _score(conference, options) > _score(existing, options):
                unique[key] = conference

        ranked = list(unique.values())
        for conference in ranked:
            _score(conference, options)
        ranked.sort(key=lambda item: (-item.score, item.event_date or "9999-12-31", item.name))
        self.last_diagnostics.extend(
            [
                {
                    "provider": "relevance_filter",
                    "status": "completed",
                    "results": excluded_relevance,
                },
                {
                    "provider": "geography_filter",
                    "status": "completed",
                    "results": excluded_geography,
                },
            ]
        )
        self.last_diagnostics.sort(key=lambda value: str(value["provider"]))
        return ranked[: options.max_results]
