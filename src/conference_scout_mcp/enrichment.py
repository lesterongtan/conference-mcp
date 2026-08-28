from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .models import ContactEvidence
from .safe_http import fetch_public_text
from .security import validate_public_url

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%.-]+/?", re.I)
JUNK_EMAIL_PARTS = ("example.", "sentry.", "wixpress", "cloudflare", "noreply", "no-reply")
GENERIC_NAME_WORDS = {
    "application",
    "conference",
    "contact",
    "none",
    "organizer",
    "request",
    "speaker",
    "submit",
    "support",
    "team",
    "unknown",
}


def _valid_email(value: str) -> str:
    for candidate in re.split(r"[,;\s]+", value or ""):
        match = EMAIL_RE.fullmatch(candidate.strip("<>.,;()[]"))
        if match and not any(part in match.group(0).lower() for part in JUNK_EMAIL_PARTS):
            return match.group(0)
    return ""


def _valid_person_name(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip("-:|,.")
    words = cleaned.split()
    if not 2 <= len(words) <= 6:
        return ""
    if any(word.casefold() in GENERIC_NAME_WORDS for word in words):
        return ""
    if not all(re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+", word) for word in words):
        return ""
    return cleaned[:200]


def _confidence(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def _contacts_from_text(text: str, url: str, provider: str, confidence: float) -> ContactEvidence:
    email = ""
    for match in EMAIL_RE.finditer(text or ""):
        email = _valid_email(match.group(0))
        if email:
            break
    linkedin = ""
    linked_match = LINKEDIN_RE.search(text or "")
    if linked_match:
        linkedin = linked_match.group(0)
    return ContactEvidence(
        contact_email=email,
        contact_linkedin=linkedin,
        source_urls=[url] if email or linkedin else [],
        provider=provider,
        confidence=confidence if email or linkedin else 0.0,
    )


class EnrichmentProvider(Protocol):
    name: str

    def enrich(self, conference: dict[str, Any], strategy: str = "") -> ContactEvidence: ...


class DirectPageEnricher:
    name = "direct_page"

    def enrich(self, conference: dict[str, Any], strategy: str = "") -> ContactEvidence:
        url = validate_public_url(conference.get("url", ""))
        text, final_url, _ = fetch_public_text(url)
        soup = BeautifulSoup(text, "html.parser")
        return _contacts_from_text(
            soup.get_text(" ", strip=True) + " " + text, final_url, self.name, 0.65
        )


class FirecrawlEnricher:
    name = "firecrawl"

    def __init__(self, api_key: str = "") -> None:
        settings = Settings()
        self.api_key = api_key or settings.firecrawl_api_key
        self.max_pages = max(1, min(settings.firecrawl_max_pages, 5))

    def enrich(self, conference: dict[str, Any], strategy: str = "") -> ContactEvidence:
        if not self.api_key:
            return ContactEvidence(provider=self.name)
        first_url = validate_public_url(conference.get("url", ""))
        origin = urlparse(first_url).netloc.lower()
        queue = [first_url]
        submission_url = str(conference.get("submission_url") or "")
        if submission_url:
            candidate = validate_public_url(urljoin(first_url, submission_url))
            if urlparse(candidate).netloc.lower() == origin and candidate not in queue:
                queue.append(candidate)
        visited: list[str] = []
        combined = ContactEvidence(provider=self.name)
        while queue and len(visited) < self.max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.append(url)
            text, raw_html = self._scrape(url)
            combined.raw["billable_units"] = int(combined.raw.get("billable_units", 0)) + 1
            found = _contacts_from_text(f"{text}\n{raw_html}", url, self.name, 0.75)
            for field in ("contact_email", "contact_linkedin"):
                if not getattr(combined, field) and getattr(found, field):
                    setattr(combined, field, getattr(found, field))
            if found.found_anything() and url not in combined.source_urls:
                combined.source_urls.append(url)
                combined.confidence = 0.75
            if combined.contact_email:
                break
            soup = BeautifulSoup(raw_html, "html.parser")
            links = [
                (anchor.get_text(" ", strip=True), str(anchor.get("href")))
                for anchor in soup.find_all("a", href=True)
            ]
            links.extend(re.findall(r"\[([^\]]+)\]\((https?://[^)]+|/[^)]+)\)", text))
            for link_text, href in links:
                label = f"{link_text} {href}".lower()
                if not any(
                    word in label for word in ("contact", "about", "team", "organizer", "speaker")
                ):
                    continue
                candidate = urljoin(url, href)
                try:
                    candidate = validate_public_url(candidate)
                except ValueError:
                    continue
                if urlparse(candidate).netloc.lower() == origin and candidate not in visited:
                    queue.append(candidate)
        return combined

    def _scrape(self, url: str) -> tuple[str, str]:
        response = httpx.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown", "rawHtml"]},
            timeout=45.0,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}
        return str(data.get("markdown") or ""), str(data.get("rawHtml") or "")


class SerperContactEnricher:
    name = "serper_contact"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or Settings().serper_api_key

    def enrich(self, conference: dict[str, Any], strategy: str = "search") -> ContactEvidence:
        if not self.api_key:
            return ContactEvidence(provider=self.name)
        name = " ".join(str(conference.get("name") or "").split())
        if len(name.split()) < 2:
            return ContactEvidence(provider=self.name)
        response = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": f'"{name}" organizer contact producer', "num": 5},
            timeout=20.0,
        )
        response.raise_for_status()
        evidence = ContactEvidence(provider=self.name, raw={"billable_units": 1})
        significant = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", name) if len(word) > 3]
        if len(significant) < 2:
            return evidence
        for item in response.json().get("organic", [])[:3]:
            url = str(item.get("link") or "")
            try:
                text, final_url, _ = fetch_public_text(url)
            except (httpx.HTTPError, ValueError):
                continue
            lowered = text.lower()
            if significant and not all(word in lowered for word in significant):
                continue
            found = _contacts_from_text(text, final_url, self.name, 0.6)
            if found.found_anything():
                found.raw["billable_units"] = 1
                return found
        return evidence


def _parse_contact_reply(reply: str) -> ContactEvidence:
    values: dict[str, str] = {}
    for line in str(reply or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().upper()] = value.strip()
    email = _valid_email(values.get("EMAIL", ""))
    linkedin_value = values.get("LINKEDIN", "")
    linkedin_match = LINKEDIN_RE.search(linkedin_value)
    linkedin = linkedin_match.group(0) if linkedin_match else ""
    none_values = {"none", "n/a", "not found", "unknown"}
    name = _valid_person_name(values.get("NAME", ""))
    role = values.get("ROLE", "")
    if name.lower() in none_values:
        name = ""
    if role.lower() in none_values:
        role = ""
    return ContactEvidence(
        contact_name=name[:200],
        contact_role=role[:200],
        contact_email=email,
        contact_linkedin=linkedin,
    )


class ContactFinderEnricher:
    name = "contact_finder"

    def __init__(
        self,
        base_url: str = "",
        token: str = "",
        poll_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = Settings()
        self.base_url = (base_url or settings.contact_finder_url).rstrip("/")
        self.token = token or settings.contact_finder_token
        self.poll_seconds = (
            settings.contact_finder_poll_seconds if poll_seconds is None else poll_seconds
        )
        self.timeout_seconds = (
            settings.contact_finder_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.search_first_timeout_seconds = settings.contact_finder_search_first_timeout_seconds

    def enrich(self, conference: dict[str, Any], strategy: str = "tier2") -> ContactEvidence:
        if not self.base_url:
            return ContactEvidence(provider=self.name)
        target_url = validate_public_url(conference.get("url", ""))
        parsed_service = urlparse(self.base_url)
        if parsed_service.scheme not in {"http", "https"} or not parsed_service.hostname:
            raise ValueError("CONTACT_FINDER_URL must be an absolute http(s) URL")
        service_url = self.base_url
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(timeout=15.0, headers=headers) as client:
            response = client.post(
                f"{service_url}/jobs",
                json={
                    "name": conference.get("name", ""),
                    "url": target_url,
                    "strategy": strategy or "tier2",
                },
            )
            response.raise_for_status()
            submit = response.json()
            job_id = submit.get("job_id") if isinstance(submit, dict) else None
            if not job_id:
                raise ValueError("Contact finder response is missing a job_id")
            budget = (
                self.search_first_timeout_seconds
                if strategy == "search_first"
                else self.timeout_seconds
            )
            deadline = time.monotonic() + budget
            while time.monotonic() < deadline:
                time.sleep(self.poll_seconds)
                try:
                    poll = client.get(f"{service_url}/jobs/{job_id}")
                    if poll.status_code >= 400:
                        continue
                    job = poll.json()
                except (httpx.HTTPError, ValueError):
                    continue
                if job.get("status") == "failed":
                    raise RuntimeError(str(job.get("error") or "Contact finder job failed"))
                if job.get("status") != "complete":
                    continue
                structured = job.get("result")
                if isinstance(structured, dict):
                    raw_sources = structured.get("source_urls", [])
                    if not isinstance(raw_sources, list):
                        raw_sources = []
                    source_urls: list[str] = []
                    for value in raw_sources:
                        try:
                            source_urls.append(validate_public_url(str(value)))
                        except ValueError:
                            continue
                    linkedin_value = str(structured.get("contact_linkedin") or "")
                    linkedin_match = LINKEDIN_RE.search(linkedin_value)
                    result = ContactEvidence(
                        contact_name=_valid_person_name(str(structured.get("contact_name") or "")),
                        contact_role=str(structured.get("contact_role") or "")[:200],
                        contact_email=_valid_email(str(structured.get("contact_email") or "")),
                        contact_linkedin=linkedin_match.group(0) if linkedin_match else "",
                        source_urls=source_urls,
                        confidence=_confidence(structured.get("confidence"), 0.7),
                        raw={"job_id": job_id, "billable_units": 1},
                    )
                else:
                    result = _parse_contact_reply(job.get("reply", ""))
                    result.confidence = 0.65 if result.found_anything() else 0.0
                    result.raw = {"job_id": job_id, "billable_units": 1}
                result.provider = self.name
                return result
        raise TimeoutError("Contact finder job did not complete before its deadline")


@dataclass
class ProviderRun:
    provider: str
    strategy: str
    status: str
    duration_ms: int
    evidence: ContactEvidence
    error: str = ""


class EnrichmentRouter:
    def __init__(
        self,
        direct: EnrichmentProvider | None = None,
        firecrawl: EnrichmentProvider | None = None,
        contact_finder: EnrichmentProvider | None = None,
        search: EnrichmentProvider | None = None,
    ) -> None:
        settings = Settings()
        self.direct = direct or DirectPageEnricher()
        self.firecrawl = firecrawl or FirecrawlEnricher()
        self.contact_finder = contact_finder or ContactFinderEnricher()
        self.search = search or (SerperContactEnricher() if settings.serper_api_key else None)
        self.search_first_enabled = settings.contact_finder_search_first_enabled

    def enrich(
        self, conference: dict[str, Any], mode: str
    ) -> tuple[ContactEvidence, list[ProviderRun]]:
        if mode == "tier3":
            stages = [(self.contact_finder, "tier3")]
        else:
            stages: list[tuple[EnrichmentProvider, str]] = [(self.direct, "page")]
            if mode in {"standard", "deep"}:
                stages.append((self.firecrawl, "page"))
            if mode == "deep":
                if self.search is not None:
                    stages.append((self.search, "search"))
                if self.search_first_enabled:
                    stages.append((self.contact_finder, "search_first"))
                stages.append((self.contact_finder, "tier2"))
        merged = ContactEvidence(
            contact_name=str(conference.get("contact_name") or ""),
            contact_role=str(conference.get("contact_role") or ""),
            contact_email=_valid_email(str(conference.get("contact_email") or "")),
            contact_linkedin=str(conference.get("contact_linkedin") or ""),
        )
        runs: list[ProviderRun] = []
        for provider, strategy in stages:
            if merged.contact_name and merged.contact_email:
                break
            if isinstance(provider, FirecrawlEnricher) and not provider.api_key:
                runs.append(
                    ProviderRun(
                        provider.name,
                        strategy,
                        "skipped",
                        0,
                        ContactEvidence(provider=provider.name),
                        "Provider is not configured",
                    )
                )
                continue
            if isinstance(provider, ContactFinderEnricher) and not provider.base_url:
                runs.append(
                    ProviderRun(
                        provider.name,
                        strategy,
                        "skipped",
                        0,
                        ContactEvidence(provider=provider.name),
                        "Provider is not configured",
                    )
                )
                continue
            started = time.monotonic()
            try:
                result = provider.enrich(conference, strategy)
                duration = int((time.monotonic() - started) * 1000)
                runs.append(ProviderRun(provider.name, strategy, "completed", duration, result))
                for field in ("contact_name", "contact_role", "contact_email", "contact_linkedin"):
                    if not getattr(merged, field) and getattr(result, field):
                        setattr(merged, field, getattr(result, field))
                for source_url in result.source_urls:
                    if source_url not in merged.source_urls:
                        merged.source_urls.append(source_url)
                if result.found_anything() and result.confidence >= merged.confidence:
                    merged.provider = result.provider
                    merged.confidence = result.confidence
                if (
                    provider.name == "contact_finder"
                    and strategy == "search_first"
                    and result.found_anything()
                ):
                    break
            except Exception as exc:  # providers fail open; the attempt remains auditable
                duration = int((time.monotonic() - started) * 1000)
                runs.append(
                    ProviderRun(
                        provider.name,
                        strategy,
                        "failed",
                        duration,
                        ContactEvidence(provider=provider.name),
                        str(exc)[:500],
                    )
                )
        return merged, runs
