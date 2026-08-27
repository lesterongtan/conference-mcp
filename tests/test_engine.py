from conference_scout_mcp.engine import DiscoveryEngine
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

