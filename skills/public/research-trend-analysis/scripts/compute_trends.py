#!/usr/bin/env python3
"""Trend calculation utilities for research-trend-analysis.

Takes a JSON array of papers with year/citation data and computes:
- Year-over-year growth rates per sub-topic
- Compound annual growth rates (CAGR)
- Citation velocity trends
- Emerging topic scores
- Decline detection

Input: JSON from stdin or file, with structure:
[
  {"title": "...", "year": 2024, "citations": 15, "keywords": ["transformer", "attention"]},
  ...
]

Output: JSON trend analysis to stdout.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from typing import Any


def _extract_keywords(papers: list[dict[str, Any]], min_count: int = 3) -> list[str]:
    """Extract all keywords from papers and return those appearing at least min_count times."""
    counts: dict[str, int] = defaultdict(int)
    for paper in papers:
        keywords = paper.get("keywords") or paper.get("topics") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",")]
        for kw in keywords:
            kw = kw.strip().lower()
            if kw and len(kw) > 2:
                counts[kw] += 1
    return [kw for kw, count in counts.items() if count >= min_count]


def _paper_keys(paper: dict[str, Any], keywords: list[str]) -> list[str]:
    """Match a paper against the keyword list."""
    paper_kws = paper.get("keywords") or paper.get("topics") or []
    if isinstance(paper_kws, str):
        paper_kws = [k.strip().lower() for k in paper_kws.split(",")]
    else:
        paper_kws = [str(k).strip().lower() for k in paper_kws]

    # Also check title for keyword mentions
    title = str(paper.get("title", "")).lower()
    matched = set()
    for kw_key in keywords:
        if kw_key in paper_kws or kw_key in title:
            matched.add(kw_key)
    return list(matched)


def compute_trends(
    papers: list[dict[str, Any]],
    keywords: list[str] | None = None,
    year_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Compute trend metrics for a set of papers."""

    # Determine keywords if not provided
    if keywords is None:
        keywords = _extract_keywords(papers)

    # Determine year range
    years_present = sorted({
        int(p.get("year", 0)) for p in papers if p.get("year")
    })
    if not years_present:
        return {"error": "No valid years in paper data"}

    start_year = year_range[0] if year_range else min(years_present)
    end_year = year_range[1] if year_range else max(years_present)

    # Organize papers by keyword and year
    kw_year_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    kw_year_citations: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    kw_paper_ids: dict[str, set[str]] = defaultdict(set)

    for paper in papers:
        year = int(paper.get("year", 0))
        if year < start_year or year > end_year:
            continue
        citations = int(paper.get("citations", paper.get("citationCount", 0)))
        paper_id = paper.get("paperId", paper.get("id", str(hash(paper.get("title", "")))))

        matched = _paper_keys(paper, keywords)
        for kw in matched:
            kw_year_counts[kw][year] += 1
            kw_year_citations[kw][year].append(citations)
            kw_paper_ids[kw].add(paper_id)

    # Compute metrics per keyword
    trends: list[dict[str, Any]] = []
    for kw in sorted(keywords):
        counts = dict(kw_year_counts.get(kw, {}))
        cit_data = dict(kw_year_citations.get(kw, {}))
        paper_count = len(kw_paper_ids.get(kw, set()))

        if paper_count < 2:
            continue

        # Fill in zeros for missing years
        for year in range(start_year, end_year + 1):
            if year not in counts:
                counts[year] = 0
            if year not in cit_data:
                cit_data[year] = []

        sorted_counts = sorted(counts.items())
        yearly = [{"year": y, "papers": c, "avg_citations": round(sum(cit_data.get(y, [])) / max(1, len(cit_data.get(y, []))), 1)} for y, c in sorted_counts]

        # Growth rate (most recent year vs previous)
        recent_count = counts.get(end_year, 0)
        prev_count = counts.get(end_year - 1, 0)
        growth_rate = (recent_count / prev_count - 1) if prev_count > 0 else float("inf") if recent_count > 0 else 0

        # CAGR
        first_count = counts.get(start_year, 0)
        last_count = counts.get(end_year, 0)
        num_years = end_year - start_year
        if first_count > 0 and num_years > 0:
            cagr = (last_count / first_count) ** (1 / num_years) - 1
        else:
            cagr = 0

        # Citation velocity
        recent_citations = cit_data.get(end_year, [])
        prev_citations = cit_data.get(end_year - 1, [])
        avg_recent_cit = sum(recent_citations) / max(1, len(recent_citations))
        avg_prev_cit = sum(prev_citations) / max(1, len(prev_citations))
        citation_velocity = (avg_recent_cit / avg_prev_cit - 1) if avg_prev_cit > 0 else 0

        # Momentum score (0-100)
        growth_norm = min(1.0, max(0, growth_rate + 0.5)) if growth_rate != float("inf") else 1.0
        recency_weight = min(1.0, recent_count / max(1, max(c for y, c in sorted_counts)))
        momentum = round(100 * (growth_norm * 0.5 + recency_weight * 0.3 + min(1.0, max(0, citation_velocity + 0.5)) * 0.2))

        # Classification
        if growth_rate == float("inf"):
            status = "emerging"
        elif growth_rate > 0.3:
            status = "hot"
        elif growth_rate > 0.1:
            status = "growing"
        elif growth_rate > -0.1:
            status = "stable"
        elif growth_rate > -0.3:
            status = "declining"
        else:
            status = "cooling"

        # Detect consecutive decline
        declining_years = 0
        sorted_by_year = sorted(counts.items())
        for i in range(1, len(sorted_by_year)):
            if sorted_by_year[i][1] < sorted_by_year[i - 1][1]:
                declining_years += 1
            else:
                declining_years = 0

        trends.append({
            "keyword": kw,
            "paper_count": paper_count,
            "growth_rate": round(growth_rate, 3) if growth_rate != float("inf") else "inf",
            "cagr": round(cagr, 3),
            "citation_velocity": round(citation_velocity, 3),
            "momentum_score": momentum,
            "status": status,
            "consecutive_decline_years": declining_years,
            "yearly_data": yearly,
        })

    # Sort by momentum score descending
    trends.sort(key=lambda t: t["momentum_score"], reverse=True)

    # Compute overall stats
    total_papers = len(papers)
    total_growing = sum(1 for t in trends if t["status"] in ("emerging", "hot", "growing"))
    total_declining = sum(1 for t in trends if t["status"] in ("declining", "cooling"))
    trend_counts = {s: sum(1 for t in trends if t["status"] == s) for s in ("emerging", "hot", "growing", "stable", "declining", "cooling")}

    return {
        "schema_version": 1,
        "year_range": [start_year, end_year],
        "total_papers_analyzed": total_papers,
        "keywords_analyzed": len(trends),
        "overall_stats": {
            "growing_topics": total_growing,
            "declining_topics": total_declining,
            "stable_topics": total_papers - total_growing - total_declining,
            "trend_distribution": trend_counts,
        },
        "topics": trends,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute research trend metrics from paper data")
    parser.add_argument("input", nargs="?", default="-", help="JSON file path or stdin (-)")
    parser.add_argument("--keywords", nargs="*", help="Specific keywords to analyze (auto-detect if omitted)")
    parser.add_argument("--start-year", type=int, help="Start year for analysis range")
    parser.add_argument("--end-year", type=int, help="End year for analysis range")
    parser.add_argument("--min-count", type=int, default=3, help="Minimum paper count for keyword inclusion")
    parser.add_argument("--output", default="-", help="Output file (default: stdout)")
    args = parser.parse_args()

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        with open(args.input, encoding="utf-8") as f:
            raw = f.read()

    papers = json.loads(raw)
    if not isinstance(papers, list):
        print("ERROR: input must be a JSON array of paper objects", file=sys.stderr)
        sys.exit(1)

    keywords = list(args.keywords) if args.keywords else None
    year_range = None
    if args.start_year and args.end_year:
        year_range = (args.start_year, args.end_year)

    result = compute_trends(papers, keywords=keywords, year_range=year_range)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)


if __name__ == "__main__":
    main()
