from datetime import date

import pytest

from conference_scout_mcp.models import Conference, SearchOptions


def test_search_options_clean_and_dedupe_keywords() -> None:
    options = SearchOptions.create([" AI healthcare ", "ai healthcare", "Leadership"])
    assert options.keywords == ("AI healthcare", "Leadership")


def test_search_options_require_keywords() -> None:
    with pytest.raises(ValueError, match="At least one"):
        SearchOptions.create([])


def test_date_window_keeps_unknown_dates() -> None:
    options = SearchOptions.create(["AI"], min_days_out=30, max_days_out=90)
    assert Conference(name="Example", url="https://example.com").date_in_window(options)


def test_date_window_filters_known_dates() -> None:
    options = SearchOptions.create(["AI"], min_days_out=10, max_days_out=20)
    result = Conference(name="Example", url="https://example.com", event_date="2026-01-31")
    assert result.date_in_window(options, today=date(2026, 1, 1)) is False

