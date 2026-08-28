from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class SearchOptions:
    keywords: tuple[str, ...]
    geography: str = ""
    max_results: int = 25
    min_days_out: int = 0
    max_days_out: int = 365
    open_cfp_only: bool = False
    enrichment_mode: str = "basic"
    max_enrichments: int = 5

    @classmethod
    def create(
        cls,
        keywords: list[str],
        geography: str = "",
        max_results: int = 25,
        min_days_out: int = 0,
        max_days_out: int = 365,
        open_cfp_only: bool = False,
        enrichment_mode: str = "basic",
        max_enrichments: int = 5,
    ) -> SearchOptions:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in keywords:
            keyword = " ".join(str(value or "").split()).strip()
            if len(keyword) > 200:
                raise ValueError("Each search keyword must be at most 200 characters")
            if keyword and keyword.casefold() not in seen:
                cleaned.append(keyword)
                seen.add(keyword.casefold())
        if not cleaned:
            raise ValueError("At least one search keyword is required")
        if len(cleaned) > 8:
            raise ValueError("At most 8 search keywords are allowed")
        normalized_geography = " ".join(str(geography or "").split()).strip()
        if len(normalized_geography) > 200:
            raise ValueError("Geography must be at most 200 characters")
        if min_days_out < 0 or max_days_out < min_days_out:
            raise ValueError("Date window must satisfy 0 <= min_days_out <= max_days_out")
        normalized_mode = str(enrichment_mode or "basic").strip().lower()
        if normalized_mode not in {"none", "basic", "standard", "deep"}:
            raise ValueError("enrichment_mode must be one of: none, basic, standard, deep")
        return cls(
            keywords=tuple(cleaned),
            geography=normalized_geography,
            max_results=max(1, min(int(max_results), 100)),
            min_days_out=min_days_out,
            max_days_out=min(max_days_out, 1095),
            open_cfp_only=bool(open_cfp_only),
            enrichment_mode=normalized_mode,
            max_enrichments=max(0, min(int(max_enrichments), 25)),
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["keywords"] = list(self.keywords)
        return value


@dataclass
class Conference:
    name: str
    url: str
    description: str = ""
    location: str = ""
    event_date: str = ""
    event_end_date: str = ""
    cfp_status: str = "Unknown"
    cfp_deadline: str = ""
    submission_url: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_role: str = ""
    contact_linkedin: str = ""
    contact_source: str = ""
    contact_confidence: float = 0.0
    event_format: str = "Unknown"
    source: str = ""
    source_url: str = ""
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def date_in_window(self, options: SearchOptions, today: date | None = None) -> bool:
        if not self.event_date:
            return True
        try:
            event_day = date.fromisoformat(self.event_date[:10])
        except ValueError:
            return True
        current = today or date.today()
        days_out = (event_day - current).days
        return options.min_days_out <= days_out <= options.max_days_out


@dataclass
class ContactEvidence:
    contact_name: str = ""
    contact_role: str = ""
    contact_email: str = ""
    contact_linkedin: str = ""
    source_urls: list[str] = field(default_factory=list)
    provider: str = ""
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def found_anything(self) -> bool:
        return any((self.contact_name, self.contact_email, self.contact_linkedin))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
