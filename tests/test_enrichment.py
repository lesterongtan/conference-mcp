import socket

import httpx
import respx

from conference_scout_mcp.enrichment import (
    ContactFinderEnricher,
    EnrichmentRouter,
    FirecrawlEnricher,
)
from conference_scout_mcp.models import ContactEvidence


class FakeEnricher:
    def __init__(
        self, name: str, result: ContactEvidence | None = None, error: Exception | None = None
    ) -> None:
        self.name = name
        self.result = result or ContactEvidence(provider=name)
        self.error = error

    def enrich(self, conference, strategy="") -> ContactEvidence:
        if self.error:
            raise self.error
        return self.result


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_router_fails_open_and_merges_only_missing_fields() -> None:
    direct = FakeEnricher("direct", error=RuntimeError("blocked"))
    firecrawl = FakeEnricher(
        "firecrawl",
        ContactEvidence(
            contact_email="events@example.org",
            provider="firecrawl",
            confidence=0.7,
            source_urls=["https://example.org/contact"],
        ),
    )
    contact_finder = FakeEnricher(
        "contact_finder",
        ContactEvidence(
            contact_name="Alex Smith",
            contact_email="other@example.org",
            provider="contact_finder",
            confidence=0.8,
        ),
    )
    router = EnrichmentRouter(direct, firecrawl, contact_finder)

    result, attempts = router.enrich({"name": "Summit", "url": "https://example.org"}, "deep")

    assert result.contact_email == "events@example.org"
    assert result.contact_name == "Alex Smith"
    assert [attempt.status for attempt in attempts] == ["failed", "completed", "completed"]


@respx.mock
def test_firecrawl_requests_markdown_and_raw_html(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    route = respx.post("https://api.firecrawl.dev/v1/scrape").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"markdown": "Contact events@aisummit.org", "rawHtml": "<html></html>"}},
        )
    )

    result = FirecrawlEnricher("secret").enrich({"url": "https://example.org"})

    assert result.contact_email == "events@aisummit.org"
    assert route.calls[0].request.headers["authorization"] == "Bearer secret"
    assert route.calls[0].request.content


@respx.mock
def test_contact_finder_job_poll_contract(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    respx.post("https://finder.example/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": "job-1"})
    )
    respx.get("https://finder.example/jobs/job-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "complete",
                "reply": (
                    "NAME: Alex Smith\nEMAIL: alex@aisummit.org\nLINKEDIN: NONE\nROLE: Producer"
                ),
            },
        )
    )

    result = ContactFinderEnricher(
        "https://finder.example", "token", poll_seconds=0, timeout_seconds=2
    ).enrich({"name": "AI Summit", "url": "https://example.org"}, "tier2")

    assert result.contact_name == "Alex Smith"
    assert result.contact_email == "alex@aisummit.org"
    assert result.contact_role == "Producer"
    assert result.provider == "contact_finder"
