from conference_scout_mcp.engine import DiscoveryEngine, _matches_geography
from conference_scout_mcp.models import Conference, SearchOptions


class FakeProvider:
    name = "fake"

    def discover(self, options: SearchOptions) -> list[Conference]:
        return [
            Conference(
                name="Healthcare AI Summit",
                url="https://example.com/summit",
                description="AI healthcare leadership",
                cfp_status="Open",
                submission_url="https://example.com/cfp",
            ),
            Conference(
                name="Duplicate",
                url="https://example.com/summit",
                description="less useful",
            ),
        ]


def test_engine_scores_and_deduplicates() -> None:
    options = SearchOptions.create(["AI", "healthcare"], max_results=10)
    results = DiscoveryEngine([FakeProvider()]).search(options)
    assert len(results) == 1
    assert results[0].name == "Healthcare AI Summit"
    assert results[0].cfp_status == "Open"
    assert results[0].score > 50


def test_open_cfp_filter() -> None:
    options = SearchOptions.create(["AI"], open_cfp_only=True)
    results = DiscoveryEngine([FakeProvider()]).search(options)
    assert all(result.cfp_status == "Open" for result in results)


class MixedQualityProvider:
    name = "mixed"
    configured = True

    def discover(self, options: SearchOptions) -> list[Conference]:
        return [
            Conference(
                name="Healthcare AI Summit",
                url="https://example.com/good",
                description="Artificial intelligence for clinical healthcare",
                location="Boston, MA, USA",
                cfp_status="Open",
            ),
            Conference(
                name="Healthcare AI Europe",
                url="https://example.com/europe",
                description="Artificial intelligence for clinical healthcare",
                location="Rome, Italy",
                cfp_status="Open",
            ),
            Conference(
                name="Generic Cloud Conference",
                url="https://example.com/cloud",
                description="Infrastructure and developer operations",
                location="Austin, TX, USA",
                cfp_status="Open",
            ),
        ]


def test_engine_strictly_filters_geography_and_relevance() -> None:
    options = SearchOptions.create(
        ["AI in healthcare"], geography="United States", open_cfp_only=True
    )
    engine = DiscoveryEngine([MixedQualityProvider()])

    results = engine.search(options)

    assert [result.name for result in results] == ["Healthcare AI Summit"]
    assert "AI in healthcare" in results[0].matched_keywords
    diagnostics = {item["provider"]: item for item in engine.last_diagnostics}
    assert diagnostics["geography_filter"]["results"] == 1
    assert diagnostics["relevance_filter"]["results"] == 1


class DisabledProvider:
    name = "disabled"
    configured = False

    def discover(self, options: SearchOptions) -> list[Conference]:
        raise AssertionError("Disabled providers must not be called")


def test_engine_reports_skipped_provider() -> None:
    engine = DiscoveryEngine([DisabledProvider(), FakeProvider()])

    engine.search(SearchOptions.create(["AI"]))

    assert any(
        warning.startswith("provider:disabled:skipped")
        for warning in engine.diagnostic_warnings()
    )


def test_us_geography_does_not_match_australia_or_vilnius() -> None:
    assert _matches_geography("Sydney, Australia", "United States") is False
    assert _matches_geography("Vilnius, Lithuania", "United States") is False
    assert _matches_geography("Boston, MA", "United States") is True


def test_business_intelligence_is_not_treated_as_artificial_intelligence() -> None:
    class BusinessIntelligenceProvider:
        name = "business-intelligence"
        configured = True

        def discover(self, options: SearchOptions) -> list[Conference]:
            return [
                Conference(
                    name="Business Intelligence Healthcare Forum",
                    url="https://example.com/bi",
                    description="Analytics dashboards for health systems",
                )
            ]

    results = DiscoveryEngine([BusinessIntelligenceProvider()]).search(
        SearchOptions.create(["artificial intelligence in healthcare"])
    )

    assert results == []
