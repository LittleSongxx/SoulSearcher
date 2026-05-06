from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


_PRIMARY_DOMAIN_MARKERS = (
    ".gov",
    ".edu",
    ".mil",
    ".int",
    "who.int",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "sec.gov",
    "fda.gov",
    "ec.europa.eu",
    "europa.eu",
    "un.org",
    "nist.gov",
    "nih.gov",
    "arxiv.org",
)

_LOW_VALUE_DOMAIN_MARKERS = (
    "medium.com",
    "substack.com",
    "quora.com",
    "reddit.com",
    "pinterest.",
    "facebook.com",
    "twitter.com",
    "x.com",
)

_OFFICIAL_HINTS = (
    "official",
    "press release",
    "annual report",
    "regulatory",
    "filing",
    "white paper",
    "documentation",
    "官方",
    "公告",
    "年报",
    "监管",
    "文件",
)


@dataclass
class SourceQualityArtifact:
    source_count: int = 0
    unique_domain_count: int = 0
    provider_count: int = 0
    primary_source_count: int = 0
    low_value_source_count: int = 0
    dated_source_count: int = 0
    fresh_source_count: int = 0
    source_diversity_score: float = 0.0
    provider_diversity_score: float = 0.0
    primary_source_ratio: float = 0.0
    low_value_source_ratio: float = 0.0
    freshness_ratio: float = 0.0
    credibility_score: float = 0.0
    domains: list[str] = field(default_factory=list)
    providers: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


def build_source_quality_artifact(
    *,
    sources: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
    search_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_sources(sources, evidence_items, search_runs)
    source_count = len(normalized)
    if source_count == 0:
        return SourceQualityArtifact(warnings=["no sources available"]).to_dict()

    domains = sorted({item["domain"] for item in normalized if item.get("domain")})
    providers: dict[str, int] = {}
    primary_count = 0
    low_value_count = 0
    dated_count = 0
    fresh_count = 0
    for item in normalized:
        provider = item.get("provider") or "unknown"
        providers[provider] = providers.get(provider, 0) + 1
        if _is_primary_source(item):
            primary_count += 1
        if _is_low_value_source(item):
            low_value_count += 1
        if item.get("date_hint"):
            dated_count += 1
            if item.get("fresh_hint"):
                fresh_count += 1

    source_diversity = min(1.0, len(domains) / max(1, min(source_count, 10)))
    provider_diversity = min(1.0, len(providers) / max(1, min(source_count, 4)))
    primary_ratio = primary_count / max(1, source_count)
    low_value_ratio = low_value_count / max(1, source_count)
    freshness_ratio = fresh_count / max(1, dated_count) if dated_count else 0.0
    credibility = max(
        0.0,
        min(
            1.0,
            0.35 * source_diversity
            + 0.2 * provider_diversity
            + 0.35 * primary_ratio
            + 0.1 * (1.0 - low_value_ratio),
        ),
    )
    warnings: list[str] = []
    if source_diversity < 0.4:
        warnings.append("low source diversity")
    if primary_ratio < 0.2 and source_count >= 5:
        warnings.append("low primary-source ratio")
    if low_value_ratio > 0.35:
        warnings.append("many low-value or social sources")
    if dated_count >= 3 and freshness_ratio < 0.3:
        warnings.append("dated sources are mostly stale")

    return SourceQualityArtifact(
        source_count=source_count,
        unique_domain_count=len(domains),
        provider_count=len(providers),
        primary_source_count=primary_count,
        low_value_source_count=low_value_count,
        dated_source_count=dated_count,
        fresh_source_count=fresh_count,
        source_diversity_score=round(source_diversity, 3),
        provider_diversity_score=round(provider_diversity, 3),
        primary_source_ratio=round(primary_ratio, 3),
        low_value_source_ratio=round(low_value_ratio, 3),
        freshness_ratio=round(freshness_ratio, 3),
        credibility_score=round(credibility, 3),
        domains=domains[:20],
        providers=providers,
        warnings=warnings,
    ).to_dict()


def quality_summary_from_source_quality(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_diversity_score": artifact.get("source_diversity_score", 0.0),
        "provider_diversity_score": artifact.get("provider_diversity_score", 0.0),
        "primary_source_ratio": artifact.get("primary_source_ratio", 0.0),
        "low_value_source_ratio": artifact.get("low_value_source_ratio", 0.0),
        "source_credibility_score": artifact.get("credibility_score", 0.0),
        "source_quality_warning_count": len(artifact.get("warnings") or []),
    }


def _normalize_sources(
    sources: list[dict[str, Any]] | None,
    evidence_items: list[dict[str, Any]] | None,
    search_runs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any], *, provider_hint: str = "") -> None:
        url = str(item.get("url") or item.get("source_url") or item.get("href") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        domain = _domain(url)
        key = url or f"{domain}|{title}"
        if not key or key in seen:
            return
        seen.add(key)
        text = " ".join(
            str(item.get(k) or "") for k in ("title", "snippet", "summary", "quote", "text")
        )
        rows.append(
            {
                "url": url,
                "title": title,
                "domain": domain,
                "provider": str(item.get("provider") or item.get("source_type") or provider_hint or "unknown"),
                "text": text,
                "date_hint": _has_date_hint(item, text),
                "fresh_hint": _has_fresh_hint(item, text),
            }
        )

    for source in sources or []:
        if isinstance(source, dict):
            add(source)
    for item in evidence_items or []:
        if isinstance(item, dict):
            add(item)
    for run in search_runs or []:
        if not isinstance(run, dict):
            continue
        provider_hint = str(run.get("provider") or run.get("strategy") or "")
        for result in run.get("results") or []:
            if isinstance(result, dict):
                add(result, provider_hint=provider_hint)
    return rows


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _is_primary_source(item: dict[str, Any]) -> bool:
    domain = str(item.get("domain") or "").lower()
    text = str(item.get("text") or "").lower()
    return any(marker in domain for marker in _PRIMARY_DOMAIN_MARKERS) or any(
        hint in text for hint in _OFFICIAL_HINTS
    )


def _is_low_value_source(item: dict[str, Any]) -> bool:
    domain = str(item.get("domain") or "").lower()
    return any(marker in domain for marker in _LOW_VALUE_DOMAIN_MARKERS)


def _has_date_hint(item: dict[str, Any], text: str) -> bool:
    if item.get("published_date") or item.get("date") or item.get("retrieved_at"):
        return True
    return bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b20\d{2}\b", text or ""))


def _has_fresh_hint(item: dict[str, Any], text: str) -> bool:
    value = " ".join(
        str(item.get(k) or "") for k in ("published_date", "date", "retrieved_at")
    )
    haystack = f"{value} {text}".lower()
    return bool(re.search(r"\b202[5-9]\b|latest|recent|today|最新|近期|今天|当前", haystack))
