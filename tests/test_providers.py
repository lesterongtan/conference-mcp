import httpx
import respx

from conference_scout_mcp.config import Settings
from conference_scout_mcp.models import SearchOptions
from conference_scout_mcp.providers import (
    ApifyDirectoryProvider,
    ConfsTechProvider,
    scrape_conference,
)


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


def test_apify_input_prefers_domain_category_and_maps_us_geography() -> None:
    settings = Settings(
        apify_api_token="apify-token",
        apify_actor_id="zen-studio~10times-events-scraper",
    )
    provider = ApifyDirectoryProvider(settings)

    payload = provider._input(
        "AI in healthcare",
        SearchOptions.create(["AI in healthcare"], geography="United States"),
    )

    assert payload["category"] == "27"
    assert payload["country"] == "US"
    assert payload["scrapeDetails"] is False


@respx.mock
def test_apify_provider_maps_directory_result_and_verifies_open_cfp() -> None:
    settings = Settings(
        apify_api_token="apify-token",
        apify_actor_id="zen-studio~10times-events-scraper",
        apify_max_keywords=1,
        discovery_max_pages=5,
    )
    provider = ApifyDirectoryProvider(settings)
    start = respx.post(
        "https://api.apify.com/v2/acts/zen-studio~10times-events-scraper/runs"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "id": "run-1",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "dataset-1",
                }
            },
        )
    )
    respx.get("https://api.apify.com/v2/datasets/dataset-1/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "Healthcare AI Leadership Summit",
                    "website": "https://example.org/health-ai",
                    "url": "https://10times.example/event",
                    "description": "Artificial intelligence for clinical healthcare leaders",
                    "startDate": "2026-10-20",
                    "location": {
                        "venueName": "Convention Center",
                        "cityName": "Austin",
                        "state": "TX",
                        "countryName": "United States",
                    },
                }
            ],
        )
    )
    respx.get("https://example.org/health-ai").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><head><title>Healthcare AI Leadership Summit</title></head>
            <body><a href="/cfp">Call for speakers — applications open</a></body></html>
            """,
            headers={"content-type": "text/html"},
        )
    )

    results = provider.discover(
        SearchOptions.create(
            ["AI in healthcare"], geography="United States", open_cfp_only=True
        )
    )

    assert start.called
    assert len(results) == 1
    assert results[0].name == "Healthcare AI Leadership Summit"
    assert results[0].location.endswith("United States")
    assert results[0].cfp_status == "Open"
    assert results[0].submission_url == "https://example.org/cfp"
    assert results[0].source == "apify_directory"
