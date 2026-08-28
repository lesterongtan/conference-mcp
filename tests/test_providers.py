import httpx
import respx

from conference_scout_mcp.models import SearchOptions
from conference_scout_mcp.providers import ConfsTechProvider, scrape_conference


@respx.mock
def test_confs_tech_provider_maps_structured_fields() -> None:
    provider = ConfsTechProvider()
    respx.get(f"{provider.base_url}/2026/python.json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "PyTest Conference",
                    "url": "https://example.com/event",
                    "startDate": "2026-10-10",
                    "endDate": "2026-10-12",
                    "city": "Austin",
                    "country": "USA",
                    "cfpUrl": "https://example.com/cfp",
                    "cfpEndDate": "2026-09-15",
                }
            ],
        )
    )
    respx.get(f"{provider.base_url}/2027/python.json").mock(return_value=httpx.Response(404))

    results = provider.discover(SearchOptions.create(["python"]))

    assert len(results) == 1
    assert results[0].name == "PyTest Conference"
    assert results[0].cfp_status == "Open"
    assert results[0].submission_url == "https://example.com/cfp"


@respx.mock
def test_scrape_conference_extracts_jsonld_and_cfp_link() -> None:
    page = """
    <html><head>
      <script type="application/ld+json">
        {"@type":"Event","name":"Health AI Forum","startDate":"2026-11-12",
         "location":{"@type":"Place","name":"Convention Center",
         "address":{"addressLocality":"Boston","addressRegion":"MA",
         "addressCountry":"USA"}}}
      </script>
    </head><body>
      <a href="/speakers/apply">Call for speakers — apply by September 30, 2026</a>
      <p>Questions: program@example.org</p>
    </body></html>
    """
    respx.get("https://example.org/forum").mock(
        return_value=httpx.Response(200, text=page, headers={"content-type": "text/html"})
    )

    result = scrape_conference("https://example.org/forum")

    assert result is not None
    assert result.name == "Health AI Forum"
    assert result.location == "Boston, MA, USA"
    assert result.cfp_status == "Open"
    assert result.submission_url == "https://example.org/speakers/apply"
    assert result.contact_email == "program@example.org"
