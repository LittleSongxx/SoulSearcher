"""
Deterministic query strategy helpers for deepsearch.

Adds lightweight coverage controls so each deepsearch run explores
multiple evidence dimensions instead of relying only on LLM sampling.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

_PUBLISHED_DATE_FIELDS = (
    "published_date",
    "publishedDate",
    "datePublished",
    "publishedAt",
    "published_at",
    "date_published",
    "pubDate",
    "displayDate",
    "date",
    "timestamp",
)

_PUBLISHED_DATE_TEXT_FIELDS = (
    "displayDate",
    "snippet",
    "summary",
    "raw_excerpt",
    "content",
    "title",
)

_MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

_QUERY_DIMENSIONS = (
    "freshness",
    "official",
    "evidence",
    "risk",
    "implementation",
)

_EN_TIME_MARKERS = (
    "latest",
    "recent",
    "today",
    "current",
    "update",
    "updates",
    "new",
    "this week",
    "this month",
    "news",
)

_ZH_TIME_MARKERS = (
    "最新",
    "近期",
    "今天",
    "当下",
    "更新",
    "本周",
    "本月",
    "动态",
    "新闻",
)

_OFFICIAL_MARKERS = (
    "official",
    "documentation",
    "docs",
    "release notes",
    "changelog",
    "roadmap",
    "官方",
    "文档",
    "发布说明",
    "路线图",
)

_EVIDENCE_MARKERS = (
    "benchmark",
    "evaluation",
    "metrics",
    "data",
    "report",
    "study",
    "paper",
    "评测",
    "评估",
    "指标",
    "数据",
    "报告",
    "论文",
)

_RISK_MARKERS = (
    "risk",
    "risks",
    "limitation",
    "limitations",
    "criticism",
    "criticisms",
    "tradeoff",
    "trade-offs",
    "争议",
    "风险",
    "局限",
    "缺点",
    "问题",
)

_IMPLEMENTATION_MARKERS = (
    "implementation",
    "how to",
    "best practices",
    "case study",
    "architecture",
    "playbook",
    "实践",
    "案例",
    "最佳实践",
    "架构",
    "落地",
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_YEAR_RE = re.compile(r"\b20\d{2}\b")


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _contains_any_raw(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_cjk_text(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def is_time_sensitive_topic(topic: str) -> bool:
    """Return whether a topic explicitly asks for recent/fresh information."""
    text = str(topic or "").strip()
    if not text:
        return False

    if _contains_any(text, _EN_TIME_MARKERS):
        return True
    if _contains_any_raw(text, _ZH_TIME_MARKERS):
        return True
    return bool(_YEAR_RE.search(text))


def query_dimensions(query: str) -> set[str]:
    """Infer coverage dimensions represented by a query."""
    text = str(query or "").strip()
    if not text:
        return set()

    dims: set[str] = set()

    if is_time_sensitive_topic(text):
        dims.add("freshness")

    if _contains_any(text, _OFFICIAL_MARKERS) or _contains_any_raw(text, _OFFICIAL_MARKERS):
        dims.add("official")

    if _contains_any(text, _EVIDENCE_MARKERS) or _contains_any_raw(text, _EVIDENCE_MARKERS):
        dims.add("evidence")

    if _contains_any(text, _RISK_MARKERS) or _contains_any_raw(text, _RISK_MARKERS):
        dims.add("risk")

    if _contains_any(text, _IMPLEMENTATION_MARKERS) or _contains_any_raw(
        text, _IMPLEMENTATION_MARKERS
    ):
        dims.add("implementation")

    return dims


def analyze_query_coverage(queries: list[str]) -> dict[str, Any]:
    """Compute dimension coverage score for generated research queries."""
    hits = {name: 0 for name in _QUERY_DIMENSIONS}

    for query in queries or []:
        for dim in query_dimensions(query):
            if dim in hits:
                hits[dim] += 1

    covered = sorted([name for name, count in hits.items() if count > 0])
    missing = sorted([name for name in _QUERY_DIMENSIONS if hits.get(name, 0) == 0])

    score = 0.0
    if _QUERY_DIMENSIONS:
        score = round(len(covered) / len(_QUERY_DIMENSIONS), 3)

    return {
        "score": score,
        "covered_dimensions": covered,
        "missing_dimensions": missing,
        "dimension_hits": hits,
        "total_queries": len(queries or []),
    }


def _seed_templates(topic: str, year: int) -> list[dict[str, str]]:
    if _is_cjk_text(topic):
        return [
            {"dimension": "freshness", "query": f"{topic} 最新进展 {year}"},
            {"dimension": "official", "query": f"{topic} 官方文档 发布说明"},
            {"dimension": "evidence", "query": f"{topic} 数据 报告 评测"},
            {"dimension": "risk", "query": f"{topic} 局限 风险 争议"},
            {"dimension": "implementation", "query": f"{topic} 实践 案例 最佳实践"},
        ]

    return [
        {"dimension": "freshness", "query": f"{topic} latest updates {year}"},
        {"dimension": "official", "query": f"{topic} official documentation release notes"},
        {"dimension": "evidence", "query": f"{topic} benchmark evaluation metrics"},
        {"dimension": "risk", "query": f"{topic} limitations risks tradeoffs"},
        {
            "dimension": "implementation",
            "query": f"{topic} implementation best practices case study",
        },
    ]


def backfill_diverse_queries(
    topic: str,
    existing_queries: list[str],
    historical_queries: list[str],
    query_num: int,
) -> list[str]:
    """
    Backfill query list with deterministic dimension seeds.

    Keeps existing LLM-generated queries first, only filling missing slots.
    """
    target = max(1, int(query_num or 1))

    seen = {
        str(q).strip().lower()
        for q in (historical_queries or [])
        if isinstance(q, str) and str(q).strip()
    }

    final_queries: list[str] = []
    for query in existing_queries or []:
        q = str(query or "").strip()
        if not q:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        final_queries.append(q)
        if len(final_queries) >= target:
            return final_queries[:target]

    coverage = analyze_query_coverage(final_queries)
    missing = set(coverage.get("missing_dimensions", []))

    seeds = _seed_templates(topic=str(topic or "").strip() or "topic", year=datetime.now().year)

    prioritized = [seed for seed in seeds if seed["dimension"] in missing]
    prioritized.extend([seed for seed in seeds if seed["dimension"] not in missing])

    for seed in prioritized:
        query = seed["query"].strip()
        key = query.lower()
        if not query or key in seen:
            continue
        seen.add(key)
        final_queries.append(query)
        if len(final_queries) >= target:
            break

    return final_queries[:target]


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_relative_datetime(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    value = str(text or "").strip().lower()
    if not value:
        return None
    base = _coerce_utc(now or datetime.now(UTC))

    if re.search(r"\bjust now\b|\btoday\b|刚刚|今天", value):
        return base
    if re.search(r"\byesterday\b|昨天", value):
        return base - timedelta(days=1)
    if re.search(r"前天", value):
        return base - timedelta(days=2)

    match = re.search(
        r"\b(\d{1,4})\s*(minute|minutes|min|mins|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago\b",
        value,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith(("minute", "min")):
            return base - timedelta(minutes=amount)
        if unit.startswith("hour"):
            return base - timedelta(hours=amount)
        if unit.startswith("day"):
            return base - timedelta(days=amount)
        if unit.startswith("week"):
            return base - timedelta(days=amount * 7)
        if unit.startswith("month"):
            return base - timedelta(days=amount * 30)
        if unit.startswith("year"):
            return base - timedelta(days=amount * 365)

    match = re.search(r"(\d{1,4})\s*(分钟|小时|天|日|周|星期|个月|月|年)前", value)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "分钟":
            return base - timedelta(minutes=amount)
        if unit == "小时":
            return base - timedelta(hours=amount)
        if unit in {"天", "日"}:
            return base - timedelta(days=amount)
        if unit in {"周", "星期"}:
            return base - timedelta(days=amount * 7)
        if unit in {"个月", "月"}:
            return base - timedelta(days=amount * 30)
        if unit == "年":
            return base - timedelta(days=amount * 365)

    return None


def _date_candidates(text: str) -> list[str]:
    value = str(text or "")
    candidates: list[str] = []
    patterns = (
        r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b",
        r"20\d{2}年\d{1,2}月\d{1,2}日?",
        rf"\b(?:{_MONTH_PATTERN})\s+\d{{1,2}},?\s+20\d{{2}}\b",
        rf"\b\d{{1,2}}\s+(?:{_MONTH_PATTERN})\s+20\d{{2}}\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            candidates.append(match.group(0))
    return candidates


def _parse_datetime(
    value: Any,
    *,
    now: Optional[datetime] = None,
    _allow_extract: bool = True,
) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return _coerce_utc(value)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None

    relative_dt = _parse_relative_datetime(text, now=now)
    if relative_dt is not None:
        return relative_dt

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text

    try:
        dt = datetime.fromisoformat(normalized)
        return _coerce_utc(dt)
    except ValueError:
        pass

    normalized_date = text.replace(".", "-").replace("/", "-")
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            dt = datetime.strptime(normalized_date, fmt)
            return _coerce_utc(dt)
        except ValueError:
            continue

    zh_match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?", text)
    if zh_match:
        try:
            return datetime(
                int(zh_match.group(1)),
                int(zh_match.group(2)),
                int(zh_match.group(3)),
                tzinfo=UTC,
            )
        except ValueError:
            return None

    if _allow_extract:
        for candidate in _date_candidates(text):
            dt = _parse_datetime(candidate, now=now, _allow_extract=False)
            if dt is not None:
                return dt
    return None


def result_published_datetime(
    result: dict[str, Any],
    *,
    run: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    containers = [result]
    if isinstance(run, dict):
        containers.append(run)

    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in _PUBLISHED_DATE_FIELDS:
            if key in container:
                dt = _parse_datetime(container.get(key), now=now)
                if dt is not None:
                    return dt

    if isinstance(result, dict):
        for key in _PUBLISHED_DATE_TEXT_FIELDS:
            if key in result:
                dt = _parse_datetime(result.get(key), now=now)
                if dt is not None:
                    return dt
    return None


def summarize_freshness(search_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize freshness distribution from collected search results."""
    total_results = 0
    known_count = 0
    unknown_count = 0
    fresh_7_count = 0
    fresh_30_count = 0
    stale_180_count = 0

    now = datetime.now(UTC)

    for run in search_runs or []:
        results = run.get("results") if isinstance(run, dict) else []
        if not isinstance(results, list):
            continue

        for result in results:
            total_results += 1
            dt = (
                result_published_datetime(result, run=run, now=now)
                if isinstance(result, dict)
                else None
            )
            if dt is None:
                unknown_count += 1
                continue

            known_count += 1
            age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            if age_days <= 7:
                fresh_7_count += 1
            if age_days <= 30:
                fresh_30_count += 1
            if age_days > 180:
                stale_180_count += 1

    fresh_30_ratio = round(fresh_30_count / known_count, 3) if known_count else 0.0
    stale_180_ratio = round(stale_180_count / known_count, 3) if known_count else 0.0

    return {
        "total_results": total_results,
        "known_count": known_count,
        "unknown_count": unknown_count,
        "fresh_7_count": fresh_7_count,
        "fresh_30_count": fresh_30_count,
        "stale_180_count": stale_180_count,
        "fresh_30_ratio": fresh_30_ratio,
        "stale_180_ratio": stale_180_ratio,
    }
