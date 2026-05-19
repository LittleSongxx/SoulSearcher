#!/usr/bin/env python3
"""Paper dependency graph builder for paper-decomposition.

Takes a JSON file of extracted claims and builds a directed dependency graph.
Identifies which claims support, depend on, contradict, or are independent of
other claims. Outputs a structured graph in JSON format.

Input: claims JSON from extract_claims.py
Output: dependency graph JSON
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Any


def _has_overlap(a: str, b: str, threshold: float = 0.3) -> bool:
    """Check if two strings share significant vocabulary."""
    words_a = set(re.findall(r"\b\w+\b", a.lower()))
    words_b = set(re.findall(r"\b\w+\b", b.lower()))
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    return len(overlap) / min(len(words_a), len(words_b)) > threshold


def _likely_supports(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    """Determine if one claim likely supports another."""
    p_text = parent.get("claim_text", "")
    c_text = child.get("claim_text", "")

    if not p_text or not c_text:
        return False

    # Empirical results support conclusions
    if parent.get("claim_type") == "empirical_result" and child.get("claim_type") in ("conclusion", "methodological"):
        return _has_overlap(p_text, c_text, 0.15)

    # Methodological claims support empirical results about the method
    if parent.get("claim_type") == "methodological" and child.get("claim_type") == "empirical_result":
        return _has_overlap(p_text, c_text, 0.2)

    return _has_overlap(p_text, c_text, 0.3)


def build_dependency_graph(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a directed dependency graph from extracted claims."""

    nodes = []
    edges = []
    graph_by_id: dict[str, dict[str, Any]] = {}

    for claim in claims:
        node = {
            "id": claim.get("claim_id", ""),
            "label": claim.get("claim_text", "")[:120],
            "type": claim.get("claim_type", "unknown"),
            "location": claim.get("location", "unknown"),
            "verifiability": claim.get("verifiability", "low"),
        }
        nodes.append(node)
        graph_by_id[node["id"]] = node

    # Build dependency edges
    for i, claim_a in enumerate(claims):
        for j, claim_b in enumerate(claims):
            if i >= j:
                continue

            aid = claim_a.get("claim_id", "")
            bid = claim_b.get("claim_id", "")

            if _likely_supports(claim_a, claim_b):
                edges.append({
                    "source": aid,
                    "target": bid,
                    "relation": "supports",
                    "confidence": "medium",
                })

            # Check for contradiction clues
            a_text = claim_a.get("claim_text", "").lower()
            b_text = claim_b.get("claim_text", "").lower()

            contradiction_markers = [
                r"\b(?:however|but|although|in\s+contrast|on\s+the\s+other\s+hand)\b",
                r"\b(?:fails?\s+to|does\s+not|doesn't|cannot)\b",
            ]

            a_has_contrast = any(re.search(m, a_text) for m in contradiction_markers)
            b_has_contrast = any(re.search(m, b_text) for m in contradiction_markers)

            if _has_overlap(a_text, b_text, 0.5) and (a_has_contrast or b_has_contrast):
                edges.append({
                    "source": aid,
                    "target": bid,
                    "relation": "contradicts",
                    "confidence": "low",
                })

    # Build connectivity metrics
    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        out_degree[edge["source"]] += 1
        in_degree[edge["target"]] += 1

    # Find root claims (no incoming edges) and leaf claims (no outgoing edges)
    root_claims = [n["id"] for n in nodes if in_degree.get(n["id"], 0) == 0]
    leaf_claims = [n["id"] for n in nodes if out_degree.get(n["id"], 0) == 0]

    return {
        "schema_version": 1,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "graph_stats": {
            "root_claims": root_claims,
            "leaf_claims": leaf_claims,
            "most_supporting": sorted(out_degree.items(), key=lambda x: -x[1])[:5],
            "most_supported": sorted(in_degree.items(), key=lambda x: -x[1])[:5],
            "isolated_claims": [n["id"] for n in nodes if in_degree.get(n["id"], 0) == 0 and out_degree.get(n["id"], 0) == 0],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build claim dependency graph")
    parser.add_argument("input", nargs="?", default="-", help="Claims JSON file or stdin")
    parser.add_argument("--output", default="-", help="Output file (default: stdout)")
    args = parser.parse_args()

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        with open(args.input, encoding="utf-8") as f:
            raw = f.read()

    data = json.loads(raw)
    claims = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(claims, list):
        print("ERROR: input must have a 'claims' array or be a claims array", file=sys.stderr)
        sys.exit(1)

    graph = build_dependency_graph(claims)

    output = json.dumps(graph, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)


if __name__ == "__main__":
    main()
