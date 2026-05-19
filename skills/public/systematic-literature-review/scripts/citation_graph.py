#!/usr/bin/env python3
"""Citation graph builder for the systematic-literature-review skill.

Traverses the Semantic Scholar citation graph starting from a set of seed paper
IDs (arXiv IDs or Semantic Scholar paper IDs). Produces structured JSON describing:

- Forward citations: papers that cite each seed paper
- Backward citations: papers cited by each seed paper
- Co-citation clusters: papers frequently cited together
- Influential citation ranking (S2's `isInfluential` flag)

API rate limits: 100 requests/5 min without key, 2× with free key.

Design:
- Respects S2 rate limits via exponential backoff
- Batches paper lookups (up to 500 papers per /batch call)
- Normalises arXiv IDs to S2 paper IDs automatically
- Outputs JSON to stdout, suitable for ingestion by the SLR skill
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_PAPER_BATCH = f"{S2_API_BASE}/paper/batch"
S2_PAPER_SEARCH = f"{S2_API_BASE}/paper/search"
S2_PAPER_CITATIONS_TEMPLATE = f"{S2_API_BASE}/paper/{{paper_id}}/citations"
S2_PAPER_REFERENCES_TEMPLATE = f"{S2_API_BASE}/paper/{{paper_id}}/references"
DEFAULT_TIMEOUT = 30
MAX_BATCH_SIZE = 500
BASE_DELAY = 1.0

CITATION_FIELDS = (
    "paperId,externalIds,title,year,authors,"
    "citationCount,influentialCitationCount,journal,publicationVenue"
)


def _request(url: str, api_key: str = "") -> dict[str, Any]:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Weaver-SLR/1.0")
    if api_key:
        req.add_header("x-api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from S2: {body[:300]}", file=sys.stderr)
        return {}


def _request_with_retry(url: str, api_key: str = "", max_retries: int = 3) -> dict[str, Any]:
    last_error = ""
    for attempt in range(max_retries):
        try:
            result = _request(url, api_key)
            if isinstance(result, dict) and result:
                return result
            last_error = f"empty response (attempt {attempt + 1})"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"  Retry {attempt + 1}/{max_retries}: {last_error}", file=sys.stderr)
        if attempt < max_retries - 1:
            delay = BASE_DELAY * (2 ** attempt)
            time.sleep(delay)
    print(f"WARNING: all {max_retries} retries exhausted: {last_error}", file=sys.stderr)
    return {}


def _arxiv_to_s2_id(arxiv_id: str, api_key: str = "") -> Optional[str]:
    """Resolve an arXiv ID to a Semantic Scholar paper ID."""
    clean = arxiv_id.strip().replace("arXiv:", "").replace("arxiv:", "")
    # arXiv IDs come in formats like 1706.03762 or 1706.03762v5
    # S2 lookup uses DOI format so we search by externalId
    params = urllib.parse.urlencode({
        "query": f"ArXiv:{clean}",
        "limit": "1",
        "fields": "paperId",
    })
    url = f"{S2_PAPER_SEARCH}?{params}"
    result = _request(url, api_key)
    data = result.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        pid = data[0].get("paperId")
        if pid:
            return str(pid)
    return None


def _batch_paper_lookup(paper_ids: list[str], fields: str, api_key: str = "") -> list[dict[str, Any]]:
    """Batch resolve paper metadata."""
    results: list[dict[str, Any]] = []
    for i in range(0, len(paper_ids), MAX_BATCH_SIZE):
        chunk = paper_ids[i : i + MAX_BATCH_SIZE]
        body = json.dumps({"ids": chunk}).encode("utf-8")
        req = urllib.request.Request(
            S2_PAPER_BATCH,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Weaver-SLR/1.0",
                **({"x-api-key": api_key} if api_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
            if isinstance(batch, list):
                results.extend(batch)
        except Exception as e:
            print(f"Batch lookup failed: {e}", file=sys.stderr)
        time.sleep(BASE_DELAY)
    return results


def _fetch_citations(paper_id: str, api_key: str = "", limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while offset < limit:
        params = urllib.parse.urlencode({
            "fields": CITATION_FIELDS,
            "limit": min(limit - offset, 500),
            "offset": offset,
        })
        url = f"{S2_PAPER_CITATIONS_TEMPLATE.format(paper_id=paper_id)}?{params}"
        result = _request_with_retry(url, api_key)
        data = result.get("data")
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
        offset += len(data)
        if len(data) < 500:
            break
        time.sleep(BASE_DELAY)
    return items


def _fetch_references(paper_id: str, api_key: str = "", limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while offset < limit:
        params = urllib.parse.urlencode({
            "fields": CITATION_FIELDS,
            "limit": min(limit - offset, 500),
            "offset": offset,
        })
        url = f"{S2_PAPER_REFERENCES_TEMPLATE.format(paper_id=paper_id)}?{params}"
        result = _request_with_retry(url, api_key)
        data = result.get("data")
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
        offset += len(data)
        if len(data) < 500:
            break
        time.sleep(BASE_DELAY)
    return items


def _find_co_citation_pairs(
    references: dict[str, list[str]],
    min_co_occurrence: int = 2,
) -> list[dict[str, Any]]:
    """Identify pairs of papers frequently cited together."""
    pair_counts: dict[tuple[str, str], int] = {}
    for ref_list in references.values():
        sorted_refs = sorted(set(ref_list))
        for i in range(len(sorted_refs)):
            for j in range(i + 1, len(sorted_refs)):
                pair = (sorted_refs[i], sorted_refs[j])
                pair_counts[pair] = pair_counts.get(pair, 0) + 1

    return [
        {"paper_a": a, "paper_b": b, "co_occurrence": count}
        for (a, b), count in sorted(pair_counts.items(), key=lambda x: -x[1])
        if count >= min_co_occurrence
    ]


def _summarise_paper(paper: dict[str, Any]) -> dict[str, Any]:
    ext = paper.get("externalIds") or {}
    authors = paper.get("authors") or []
    return {
        "paperId": paper.get("paperId", ""),
        "title": paper.get("title", ""),
        "year": paper.get("year"),
        "authors": [a.get("name", "") for a in authors[:5]],
        "citationCount": paper.get("citationCount", 0),
        "influentialCitationCount": paper.get("influentialCitationCount", 0),
        "journal": (paper.get("journal") or {}).get("name"),
        "venue": paper.get("publicationVenue"),
        "externalIds": {k: v for k, v in ext.items() if v},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build citation graph from seed paper IDs",
    )
    parser.add_argument(
        "seed_ids",
        nargs="+",
        help="arXiv IDs or Semantic Scholar paper IDs",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Semantic Scholar API key (optional, for higher rate limits)",
    )
    parser.add_argument(
        "--max-citations",
        type=int,
        default=100,
        help="Max citations to fetch per paper (default: 100)",
    )
    parser.add_argument(
        "--max-references",
        type=int,
        default=100,
        help="Max references to fetch per paper (default: 100)",
    )
    parser.add_argument(
        "--min-co-occurrence",
        type=int,
        default=2,
        help="Min co-citation count to report (default: 2)",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    api_key = (args.api_key or "").strip()

    # Resolve arXiv IDs to S2 IDs
    s2_ids: list[str] = []
    for sid in args.seed_ids:
        sid = sid.strip()
        if not sid:
            continue
        # Already an S2 ID (40-char hex)
        if len(sid) == 40 and all(c in "0123456789abcdef" for c in sid):
            s2_ids.append(sid)
        else:
            resolved = _arxiv_to_s2_id(sid, api_key)
            if resolved:
                s2_ids.append(resolved)
                print(f"Resolved arXiv:{sid} → S2:{resolved}", file=sys.stderr)
            else:
                print(f"WARNING: could not resolve {sid} to S2 ID", file=sys.stderr)

    if not s2_ids:
        print("ERROR: no valid paper IDs could be resolved", file=sys.stderr)
        sys.exit(1)

    # Fetch seed paper metadata
    print(f"Fetching metadata for {len(s2_ids)} seed papers...", file=sys.stderr)
    seed_papers = _batch_paper_lookup(s2_ids, CITATION_FIELDS, api_key)
    seed_map = {p.get("paperId", ""): _summarise_paper(p) for p in seed_papers if p.get("paperId")}

    # Fetch citations and references per seed
    forward_citations: dict[str, list[dict[str, Any]]] = {}
    backward_references: dict[str, list[dict[str, Any]]] = {}
    references_by_pool: dict[str, list[str]] = {}

    for sid in s2_ids:
        print(f"Fetching citations for {sid[:12]}...", file=sys.stderr)
        forward_citations[sid] = _fetch_citations(sid, api_key, args.max_citations)
        time.sleep(BASE_DELAY)

        print(f"Fetching references for {sid[:12]}...", file=sys.stderr)
        backward_references[sid] = _fetch_references(sid, api_key, args.max_references)
        time.sleep(BASE_DELAY)

    # Build reference sets for co-citation analysis
    for sid in s2_ids:
        refs = backward_references.get(sid, [])
        ref_ids = []
        for r in refs:
            cited = (r.get("citedPaper") or {}) if "citedPaper" in r else r
            cited_id = cited.get("paperId")
            if cited_id:
                ref_ids.append(cited_id)
        references_by_pool[sid] = ref_ids

    # Find co-citations
    print("Computing co-citation clusters...", file=sys.stderr)
    co_citations = _find_co_citation_pairs(references_by_pool, args.min_co_occurrence)

    # Build citation network metrics
    all_cited_ids: set[str] = set()
    for refs in references_by_pool.values():
        all_cited_ids.update(refs)

    all_citing_ids: set[str] = set()
    for cites in forward_citations.values():
        for c in cites:
            citing = (c.get("citingPaper") or {}) if "citingPaper" in c else c
            cid = citing.get("paperId")
            if cid:
                all_citing_ids.add(cid)

    # Resolve co-citation paper metadata
    co_cited_ids: set[str] = set()
    for cc in co_citations:
        co_cited_ids.add(cc["paper_a"])
        co_cited_ids.add(cc["paper_b"])

    lookup_ids = list(all_cited_ids | all_citing_ids | co_cited_ids)[:500]
    paper_lookup: dict[str, dict[str, Any]] = {}
    if lookup_ids:
        print(f"Resolving metadata for {len(lookup_ids)} connected papers...", file=sys.stderr)
        resolved = _batch_paper_lookup(lookup_ids, CITATION_FIELDS, api_key)
        paper_lookup = {
            p.get("paperId", ""): _summarise_paper(p)
            for p in resolved
            if p.get("paperId")
        }

    # Assemble output
    output: dict[str, Any] = {
        "schema_version": 1,
        "seed_papers": {sid: seed_map.get(sid, {}) for sid in s2_ids},
        "forward_citations": {
            sid: [
                {
                    **_summarise_paper(
                        (c.get("citingPaper") or {}) if "citingPaper" in c else c
                    ),
                    "isInfluential": c.get("isInfluential", False),
                    "contexts": c.get("contexts", []),
                    "intents": c.get("intents", []),
                }
                for c in cites[:200]
            ]
            for sid, cites in forward_citations.items()
        },
        "backward_references": {
            sid: [
                {
                    **_summarise_paper(
                        (r.get("citedPaper") or {}) if "citedPaper" in r else r
                    ),
                    "isInfluential": r.get("isInfluential", False),
                    "contexts": r.get("contexts", []),
                    "intents": r.get("intents", []),
                }
                for r in refs[:200]
            ]
            for sid, refs in backward_references.items()
        },
        "co_citation_clusters": [
            {
                **cc,
                "paper_a_meta": paper_lookup.get(cc["paper_a"], {}),
                "paper_b_meta": paper_lookup.get(cc["paper_b"], {}),
            }
            for cc in co_citations[:50]
        ],
        "network_stats": {
            "seed_count": len(s2_ids),
            "cited_paper_count": len(all_cited_ids),
            "citing_paper_count": len(all_citing_ids),
            "co_citation_pairs": len(co_citations),
            "total_papers_in_network": (
                len(s2_ids) + len(all_cited_ids) + len(all_citing_ids)
            ),
        },
        "connected_paper_lookup": {
            pid: paper_lookup.get(pid, {}) for pid in list(lookup_ids)[:200]
        },
    }

    result = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(result)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)


if __name__ == "__main__":
    main()
