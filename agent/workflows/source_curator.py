from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Optional
from urllib.parse import urlparse

from agent.workflows.research_brief import ResearchBrief
from agent.workflows.source_url_utils import canonicalize_source_url

_AUTHORITY_HINTS = (
    ".gov",
    ".gov.cn",
    ".edu",
    ".ac.",
    "europa.eu",
    "who.int",
    "fda.gov",
    "sec.gov",
    "bis.doc.gov",
    "arxiv.org",
    "nature.com",
    "science.org",
    "nejm.org",
    "thelancet.com",
    "nber.org",
    "oecd.org",
    "worldbank.org",
    "imf.org",
    "iea.org",
)
_PRIMARY_HINTS = (
    "official",
    "documentation",
    "docs",
    "annual report",
    "sustainability report",
    "10-k",
    "press release",
    "regulation",
    "guidance",
    "standard",
    "官方",
    "公告",
    "监管",
    "政策",
    "年报",
    "可持续发展报告",
    "白皮书",
)
_LOW_QUALITY_HINTS = (
    "medium.com",
    "substack.com",
    "reddit.com",
    "quora.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "linkedin.com/posts",
)
_QUANTITATIVE_RE = re.compile(r"\d{4}|\d+%|\d+\.\d+|\b(?:data|statistics|benchmark|survey|report)\b|数据|统计|基准|报告", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower()) if len(t) > 1}


def _domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _freshness_score(value: Any) -> float:
    dt = _parse_datetime(value)
    if dt is None:
        return 0.0
    age_days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400.0)
    if age_days <= 30:
        return 0.16
    if age_days <= 180:
        return 0.1
    if age_days <= 730:
        return 0.04
    return 0.0


def _source_text(source: dict[str, Any]) -> str:
    fields = [
        source.get("title"),
        source.get("name"),
        source.get("snippet"),
        source.get("summary"),
        source.get("content"),
        source.get("domain"),
        source.get("url"),
    ]
    return " ".join(_text(item) for item in fields if _text(item))


def score_source(source: dict[str, Any], *, brief: Optional[ResearchBrief] = None) -> tuple[float, list[str]]:
    url = canonicalize_source_url(source.get("url") or source.get("rawUrl")) or _text(source.get("url") or source.get("rawUrl"))
    domain = _text(source.get("domain")) or _domain(url)
    title = _text(source.get("title") or source.get("name"))
    source_text = _source_text(source)
    score = 0.35
    reasons: list[str] = []

    if any(hint in domain for hint in _AUTHORITY_HINTS):
        score += 0.25
        reasons.append("authoritative_domain")
    if any(hint in source_text.lower() for hint in _PRIMARY_HINTS):
        score += 0.18
        reasons.append("primary_source_hint")
    if any(hint in domain or hint in url.lower() for hint in _LOW_QUALITY_HINTS):
        score -= 0.2
        reasons.append("low_quality_hint")
    if _QUANTITATIVE_RE.search(source_text):
        score += 0.1
        reasons.append("quantitative_signal")

    if brief is not None:
        goal_tokens = _tokens(" ".join([brief.clarified_goal, brief.original_query, " ".join(brief.expected_fields)]))
        source_tokens = _tokens(source_text)
        if goal_tokens and source_tokens:
            overlap = len(goal_tokens & source_tokens) / max(1, min(len(goal_tokens), 20))
            score += min(0.18, overlap)
            if overlap >= 0.1:
                reasons.append("topic_overlap")
        preferred = " ".join(brief.preferred_sources or []).lower()
        if preferred and any(part and part in source_text.lower() for part in re.split(r"[,;\s]+", preferred)):
            score += 0.08
            reasons.append("preferred_source_match")

    score += _freshness_score(source.get("publishedDate") or source.get("published_date") or source.get("date"))
    if title:
        score += 0.02

    score = max(0.0, min(1.0, score))
    return round(score, 4), reasons


def curate_sources(
    sources: list[dict[str, Any]],
    *,
    brief: Optional[ResearchBrief] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    enriched: list[tuple[float, int, dict[str, Any]]] = []
    seen = set()
    for idx, source in enumerate(sources or []):
        if not isinstance(source, dict):
            continue
        url = canonicalize_source_url(source.get("url") or source.get("rawUrl")) or _text(source.get("url") or source.get("rawUrl"))
        key = url or _text(source.get("title") or source.get("name")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        score, reasons = score_source(source, brief=brief)
        item = dict(source)
        if url:
            item["url"] = url
        if not item.get("domain") and url:
            item["domain"] = _domain(url)
        item["reliability_score"] = score
        item["reliability_reasons"] = reasons
        enriched.append((score, idx, item))

    enriched.sort(key=lambda row: (-row[0], row[1]))
    output = [item for _score, _idx, item in enriched]
    if limit is not None:
        return output[: max(1, int(limit))]
    return output
