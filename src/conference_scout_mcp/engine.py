from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from .models import Conference, SearchOptions
from .providers import ConfsTechProvider, DiscoveryProvider, SerperProvider

logger = logging.getLogger(__name__)


def _identity(conference: Conference) -> str:
    parsed = urlparse(conference.url or conference.submission_url)
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/").casefold()
    if host:
        return f"{host}{path}"
    return re.sub(r"\W+", " ", conference.name.casefold()).strip()


def _score(conference: Conference, options: SearchOptions) -> float:
    haystack = " ".join((conference.name, conference.description, conference.location)).casefold()
    conference.matched_keywords = [
        keyword for keyword in options.keywords if keyword.casefold() in haystack
    ]
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
    def __init__(self, providers: list[DiscoveryProvider] | None = None) -> None:
        self.providers = providers or [ConfsTechProvider(), SerperProvider()]

    def search(self, options: SearchOptions) -> list[Conference]:
        discovered: list[Conference] = []
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as executor:
            futures = {
                executor.submit(provider.discover, options): provider for provider in self.providers
            }
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    discovered.extend(future.result())
                except Exception:
                    logger.exception("Discovery provider failed: %s", provider.name)

        unique: dict[str, Conference] = {}
        for conference in discovered:
            if not conference.name or not (conference.url or conference.submission_url):
                continue
            if not conference.date_in_window(options):
                continue
            if options.open_cfp_only and conference.cfp_status != "Open":
                continue
            key = _identity(conference)
            existing = unique.get(key)
            if existing is None or _score(conference, options) > _score(existing, options):
                unique[key] = conference

        ranked = list(unique.values())
        for conference in ranked:
            _score(conference, options)
        ranked.sort(key=lambda item: (-item.score, item.event_date or "9999-12-31", item.name))
        return ranked[: options.max_results]
