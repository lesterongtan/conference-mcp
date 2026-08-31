from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .config import Settings
from .models import Conference, SearchOptions
from .safe_http import fetch_public_text

logger = logging.getLogger(__name__)

USER_AGENT = os.getenv(
    "CONFERENCE_SCOUT_USER_AGENT",
    "ConferenceScoutMCP/0.1 (+https://speakeragent.ai)",
)
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class DiscoveryProvider(Protocol):
    name: str
    configured: bool

    def discover(self, options: SearchOptions) -> list[Conference]: ...


def _iso_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return date_parser.parse(str(value), fuzzy=True).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return ""


def _string(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, dict):
        return _string(value.get("name") or value.get("description") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_string(item) for item in value)))
    return ""


def _walk_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_jsonld(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)


def _is_event(node: dict[str, Any]) -> bool:
    kind = node.get("@type", "")
    values = kind if isinstance(kind, list) else [kind]
    return any("event" in str(value).casefold() for value in values)


def _location(node: dict[str, Any]) -> tuple[str, str]:
    location = node.get("location")
    if isinstance(location, list) and location:
        location = location[0]
    if not isinstance(location, dict):
        return _string(location), "Unknown"
    kind = _string(location.get("@type"))
    if "virtual" in kind.casefold():
        return "Online", "Virtual"
    address = location.get("address")
    if isinstance(address, dict):
        parts = [
            _string(address.get("addressLocality")),
            _string(address.get("addressRegion")),
            _string(address.get("addressCountry")),
        ]
        formatted = ", ".join(part for part in parts if part)
    else:
        formatted = _string(address)
    return formatted or _string(location.get("name")), "In person"


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
DATE_RE = re.compile(
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s*,?\s*\d{4}",
    re.I,
)
CFP_LINK_RE = re.compile(
    r"(?:cfp|call[ -]?for[ -]?(?:speakers|papers|proposals)|submit|speaker[ -]?application)",
    re.I,
)
CFP_CLOSED_RE = re.compile(
    r"(?:call for (?:speakers|papers|proposals).{0,50}closed|"
    r"submissions?.{0,30}closed|deadline has passed|no longer accepting)",
    re.I | re.S,
)
CFP_OPEN_RE = re.compile(
    r"(?:call for (?:speakers|papers|proposals)|submit (?:a )?(?:talk|proposal)|"
    r"speaker applications?).{0,60}(?:open|apply|submit|deadline)",
    re.I | re.S,
)


def scrape_conference(url: str, source: str = "web_search") -> Conference | None:
    try:
        page_text, final_url, content_type = fetch_public_text(
            url,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Could not fetch %s: %s", url, exc)
        return None

    if "html" not in content_type.casefold():
        return None
    soup = BeautifulSoup(page_text, "html.parser")
    event_node: dict[str, Any] = {}
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            parsed = json.loads(tag.string or tag.get_text() or "null")
        except json.JSONDecodeError:
            continue
        event_node = next((node for node in _walk_jsonld(parsed) if _is_event(node)), {})
        if event_node:
            break

    title = _string(event_node.get("name"))
    if not title:
        heading = soup.find("h1")
        title = _string(heading.get_text(" ") if heading else "")
    if not title and soup.title:
        title = _string(soup.title.get_text(" "))
    if not title:
        return None

    description = _string(event_node.get("description"))
    if not description:
        meta = soup.find("meta", attrs={"name": "description"})
        description = _string(meta.get("content") if meta else "")

    location, event_format = _location(event_node)
    event_date = _iso_date(event_node.get("startDate"))
    event_end_date = _iso_date(event_node.get("endDate"))
    text = " ".join(soup.stripped_strings)
    if not event_date:
        date_match = DATE_RE.search(text[:10000])
        event_date = _iso_date(date_match.group(0)) if date_match else ""

    submission_url = ""
    evidence = [final_url]
    for anchor in soup.find_all("a", href=True):
        label = " ".join(anchor.get_text(" ").split())
        href = str(anchor.get("href") or "")
        if CFP_LINK_RE.search(f"{label} {href}"):
            submission_url = urljoin(final_url, href)
            evidence.append(submission_url)
            break

    if CFP_CLOSED_RE.search(text):
        cfp_status = "Closed"
    elif submission_url or CFP_OPEN_RE.search(text):
        cfp_status = "Open"
    else:
        cfp_status = "Unknown"

    deadline = ""
    deadline_match = re.search(
        r"(?:deadline|closes?|due)\D{0,25}(" + DATE_RE.pattern + r"|\d{4}-\d{2}-\d{2})",
        text,
        re.I,
    )
    if deadline_match:
        deadline = _iso_date(deadline_match.group(1))

    email_match = EMAIL_RE.search(text)
    return Conference(
        name=title[:300],
        url=final_url,
        description=description[:1000],
        location=location[:300],
        event_date=event_date,
        event_end_date=event_end_date,
        cfp_status=cfp_status,
        cfp_deadline=deadline,
        submission_url=submission_url,
        contact_email=email_match.group(0) if email_match else "",
        event_format=event_format,
        source=source,
        source_url=url,
        evidence=list(dict.fromkeys(evidence)),
    )


class ConfsTechProvider:
    name = "confs.tech"
    configured = True
    base_url = "https://raw.githubusercontent.com/tech-conferences/conference-data/main/conferences"
    topics = {
        "android",
        "css",
        "data",
        "devops",
        "dotnet",
        "elixir",
        "general",
        "golang",
        "graphql",
        "ios",
        "java",
        "javascript",
        "kotlin",
        "leadership",
        "php",
        "python",
        "ruby",
        "rust",
        "scala",
        "security",
        "tech-comm",
        "typescript",
        "ux",
    }
    aliases = {
        "ai": "data",
        "artificial intelligence": "data",
        "machine learning": "data",
        "ml": "data",
        "cybersecurity": "security",
        "cyber security": "security",
        "software": "general",
        "technology": "general",
        "cloud": "devops",
        "kubernetes": "devops",
        "web": "javascript",
    }

    def _matching_topics(self, keywords: tuple[str, ...]) -> list[str]:
        matched: list[str] = []
        for keyword in keywords:
            normalized = keyword.casefold().strip()
            for topic in self.topics:
                if topic in normalized or normalized in topic:
                    matched.append(topic)
            for phrase, topic in self.aliases.items():
                if phrase in normalized:
                    matched.append(topic)
        return list(dict.fromkeys(matched))

    def discover(self, options: SearchOptions) -> list[Conference]:
        topics = self._matching_topics(options.keywords)
        if not topics:
            return []
        results: list[Conference] = []
        years = range(date.today().year, date.today().year + 2)
        with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            for year in years:
                for topic in topics:
                    source_url = f"{self.base_url}/{year}/{topic}.json"
                    try:
                        response = client.get(source_url)
                        if response.status_code == 404:
                            continue
                        response.raise_for_status()
                        entries = response.json()
                    except (httpx.HTTPError, ValueError):
                        continue
                    for entry in entries if isinstance(entries, list) else []:
                        cfp_url = _string(entry.get("cfpUrl"))
                        cfp_deadline = _iso_date(entry.get("cfpEndDate"))
                        cfp_status = "Open" if cfp_url else "Unknown"
                        if cfp_deadline and cfp_deadline < date.today().isoformat():
                            cfp_status = "Closed"
                        city = _string(entry.get("city"))
                        country = _string(entry.get("country"))
                        results.append(
                            Conference(
                                name=_string(entry.get("name")) or "Unnamed conference",
                                url=_string(entry.get("url")) or cfp_url,
                                location=", ".join(part for part in (city, country) if part),
                                event_date=_iso_date(entry.get("startDate")),
                                event_end_date=_iso_date(entry.get("endDate")),
                                cfp_status=cfp_status,
                                cfp_deadline=cfp_deadline,
                                submission_url=cfp_url,
                                event_format="Virtual" if entry.get("online") else "In person",
                                source=self.name,
                                source_url=source_url,
                                evidence=[source_url] + ([cfp_url] if cfp_url else []),
                            )
                        )
        return results


TENTIMES_ACTOR_ID = "zen-studio~10times-events-scraper"
TENTIMES_CATEGORY_BY_KEYWORD = {
    "healthcare": "27",
    "health": "27",
    "medical": "27",
    "medicine": "27",
    "pharma": "27",
    "pharmaceutical": "27",
    "hospital": "27",
    "clinical": "27",
    "nursing": "27",
    "biotech": "53",
    "biotechnology": "53",
    "science": "53",
    "research": "53",
    "finance": "146",
    "financial": "146",
    "banking": "146",
    "fintech": "146",
    "security": "150",
    "cybersecurity": "150",
    "education": "61",
    "training": "61",
    "technology": "156",
    "tech": "156",
    "software": "156",
    "cloud": "156",
    "ai": "156",
}
COUNTRY_CODES = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.a.": "US",
    "us": "US",
    "u.s.": "US",
    "canada": "CA",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "australia": "AU",
    "singapore": "SG",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
    "netherlands": "NL",
}


def _tentimes_category(keyword: str) -> str:
    categories = [
        TENTIMES_CATEGORY_BY_KEYWORD[word]
        for word in re.findall(r"[a-zA-Z]+", keyword.casefold())
        if word in TENTIMES_CATEGORY_BY_KEYWORD
    ]
    if not categories:
        return ""
    # A domain category is more useful than generic IT for mixed phrases such as
    # "AI in healthcare" or "AI fintech".
    return next((value for value in categories if value != "156"), categories[0])


def _apify_country(geography: str) -> str:
    return COUNTRY_CODES.get(geography.casefold().strip(), "WW")


class ApifyDirectoryProvider:
    """Discover structured events through a bounded Apify directory actor."""

    name = "apify_directory"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.api_token = self.settings.apify_api_token
        self.actor_id = self.settings.apify_actor_id
        self.configured = bool(self.api_token and self.actor_id)

    def _input(self, keyword: str, options: SearchOptions) -> dict[str, Any]:
        if self.actor_id == TENTIMES_ACTOR_ID:
            category = _tentimes_category(keyword)
            value: dict[str, Any] = {
                "category": category,
                "country": _apify_country(options.geography),
                "eventType": "conference",
                "startDate": (date.today() + timedelta(days=options.min_days_out)).isoformat(),
                "endDate": (date.today() + timedelta(days=options.max_days_out)).isoformat(),
                "maxItems": max(10, options.max_results),
                "onlineOnly": False,
                "scrapeDetails": False,
            }
            if not category:
                value["query"] = keyword
            return value
        if not self.settings.apify_input_template:
            raise ValueError(
                "CFP_DIRECTORY_APIFY_INPUT_TEMPLATE is required for a non-default actor"
            )
        rendered = self.settings.apify_input_template.replace("{keyword}", keyword).replace(
            "{geography}", options.geography
        )
        parsed = json.loads(rendered)
        if not isinstance(parsed, dict):
            raise ValueError("CFP_DIRECTORY_APIFY_INPUT_TEMPLATE must produce a JSON object")
        return parsed

    def _run_actor(self, run_input: dict[str, Any], charge_limit: float) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.api_token}"}
        with httpx.Client(timeout=httpx.Timeout(70.0, connect=10.0), headers=headers) as client:
            response = client.post(
                f"https://api.apify.com/v2/acts/{self.actor_id}/runs",
                params={"waitForFinish": 60, "maxTotalChargeUsd": charge_limit},
                json=run_input,
            )
            response.raise_for_status()
            run = response.json().get("data", {})
            run_id = str(run.get("id") or "")
            status = str(run.get("status") or "")
            deadline = time.monotonic() + max(30, self.settings.apify_max_poll_seconds)
            while run_id and status not in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Apify actor did not complete before its deadline")
                time.sleep(5)
                poll = client.get(f"https://api.apify.com/v2/actor-runs/{run_id}", timeout=30)
                poll.raise_for_status()
                run = poll.json().get("data", {})
                status = str(run.get("status") or "")
            if status != "SUCCEEDED":
                raise RuntimeError(f"Apify actor finished with status {status or 'unknown'}")
            dataset_id = str(run.get("defaultDatasetId") or "")
            if not dataset_id:
                raise RuntimeError("Apify actor response did not include a dataset")
            items = client.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items", timeout=30
            )
            items.raise_for_status()
            payload = items.json()
            if not isinstance(payload, list):
                raise ValueError("Apify dataset response was not a list")
            return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _normalize(item: dict[str, Any]) -> Conference | None:
        name = _string(item.get("name") or item.get("title") or item.get("eventName"))
        raw_url = _string(
            item.get("website") or item.get("url") or item.get("link") or item.get("eventUrl")
        )
        if not name or not raw_url:
            return None
        url = raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}"
        raw_location = item.get("location")
        if isinstance(raw_location, dict):
            location = ", ".join(
                dict.fromkeys(
                    filter(
                        None,
                        (
                            _string(raw_location.get("venueName")),
                            _string(raw_location.get("cityName")),
                            _string(raw_location.get("state")),
                            _string(raw_location.get("countryName")),
                        ),
                    )
                )
            )
        else:
            location = _string(raw_location or item.get("venue"))
        source_url = _string(item.get("url")) or url
        return Conference(
            name=name[:300],
            url=url,
            description=_string(item.get("description"))[:1000],
            location=location[:300],
            event_date=_iso_date(item.get("startDate") or item.get("date")),
            event_end_date=_iso_date(item.get("endDate")),
            cfp_status="Unknown",
            event_format="In person",
            source=ApifyDirectoryProvider.name,
            source_url=source_url,
            evidence=list(dict.fromkeys(filter(None, (source_url, url)))),
        )

    @staticmethod
    def _merge_page(directory: Conference, page: Conference | None) -> Conference:
        if page is None:
            return directory
        directory.description = directory.description or page.description
        directory.location = directory.location or page.location
        directory.event_date = directory.event_date or page.event_date
        directory.event_end_date = directory.event_end_date or page.event_end_date
        directory.cfp_status = page.cfp_status
        directory.cfp_deadline = page.cfp_deadline
        directory.submission_url = page.submission_url
        directory.contact_email = page.contact_email
        directory.event_format = page.event_format
        directory.evidence = list(dict.fromkeys([*directory.evidence, *page.evidence]))
        return directory

    def discover(self, options: SearchOptions) -> list[Conference]:
        if not self.configured:
            return []
        keywords = list(options.keywords[: max(1, self.settings.apify_max_keywords)])
        charge = max(0.01, self.settings.apify_max_charge_usd) / len(keywords)
        raw_items: list[dict[str, Any]] = []
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=len(keywords)) as executor:
            futures = {
                executor.submit(self._run_actor, self._input(keyword, options), charge): keyword
                for keyword in keywords
            }
            for future in as_completed(futures):
                try:
                    raw_items.extend(future.result())
                except Exception as exc:
                    failures.append(f"{futures[future]}: {exc}")
        normalized = [value for item in raw_items if (value := self._normalize(item))]
        candidates = normalized[: max(1, self.settings.discovery_max_pages)]
        results: list[Conference] = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(self.settings.discovery_max_concurrent, len(candidates) or 1))
        ) as executor:
            pages = {
                executor.submit(scrape_conference, candidate.url, self.name): candidate
                for candidate in candidates
            }
            for future in as_completed(pages):
                results.append(self._merge_page(pages[future], future.result()))
        if failures and not results:
            raise RuntimeError("; ".join(failures)[:500])
        if failures:
            logger.warning("Some Apify keyword runs failed: %s", "; ".join(failures))
        return results


class SerperProvider:
    name = "serper"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("SERPER_API_KEY", "")
        self.configured = bool(self.api_key)

    def discover(self, options: SearchOptions) -> list[Conference]:
        if not self.api_key:
            return []
        year = date.today().year
        geography = f" {options.geography}" if options.geography else ""
        urls: list[str] = []
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            for keyword in options.keywords:
                queries = (
                    f'{keyword} "call for speakers" conference{geography} {year}',
                    f'{keyword} "call for proposals" summit{geography} {year}',
                    f"{keyword} conference speakers{geography} {year + 1}",
                )
                for query in queries:
                    try:
                        response = client.post(
                            "https://google.serper.dev/search",
                            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                            json={"q": query, "num": 10},
                        )
                        response.raise_for_status()
                        data = response.json()
                    except (httpx.HTTPError, ValueError):
                        continue
                    for item in data.get("organic", []):
                        url = _string(item.get("link"))
                        if url.startswith(("http://", "https://")):
                            urls.append(url)

        settings = Settings()
        candidates: list[str] = []
        for url in list(dict.fromkeys(urls))[: max(1, settings.discovery_max_pages)]:
            parsed = urlparse(url)
            if parsed.path.casefold().endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx")):
                continue
            candidates.append(url)
        results: list[Conference] = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(settings.discovery_max_concurrent, 16))
        ) as executor:
            futures = [executor.submit(scrape_conference, url, self.name) for url in candidates]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
        return results
