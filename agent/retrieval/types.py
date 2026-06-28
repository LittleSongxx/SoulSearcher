from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RetrievedSource:
    id: str
    title: str
    url: str = ""
    summary: str = ""
    content: str = ""
    source_origin: str = "public_web"
    access_channel: str = "search_api"
    retrieval_method: str = "web_search"
    profile: str = "general"
    provider: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieved_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [], {})}


@dataclass
class RetrievedPassage:
    id: str
    source_id: str
    text: str
    title: str = ""
    url: str = ""
    source_origin: str = "public_web"
    access_channel: str = "crawler"
    retrieval_method: str = "crawl"
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [], {})}


@dataclass
class RetrievalResult:
    query: str
    policy: dict[str, Any]
    sources: list[dict[str, Any]] = field(default_factory=list)
    passages: list[dict[str, Any]] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
