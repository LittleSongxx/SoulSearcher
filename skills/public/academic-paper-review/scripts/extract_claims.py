#!/usr/bin/env python3
"""Structured claim extraction for academic-paper-review.

Parses a paper's text content (from uploaded PDF or fetched HTML) and extracts
structured, verifiable claims. Outputs JSON that can be fed into Weaver's
claim_verifier and fact_cards workflows for evidence-backed verification.

Each extracted claim includes:
- claim_text: The claim as stated in the paper
- claim_type: (numerical, methodological, comparative, definitional, speculative)
- location: section/figure/table reference
- verifiability: estimate of how checkable this claim is (high/medium/low)
- evidence_in_paper: any supporting evidence the paper itself provides
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Patterns for identifying different claim types
NUMERICAL_PATTERNS = (
    r"\d+\.?\d*\s*%",
    r"\b(?:accuracy|precision|recall|F1|BLEU|ROUGE|AUC|perplexity)\b[^.]*?\d+\.?\d*",
    r"\b(?:outperforms?|achieves?|reaches?|improves?\s+by)[^.]*?\d+\.?\d*\s*%",
    r"\b(?:reduces?|decreases?|lowers?|cuts?)[^.]*?\d+\.?\d*\s*%",
    r"p\s*[<>=]\s*0\.\d+",
    r"\b(?:Cohen's\s+d|effect\s+size)[^.]*?\d+\.?\d*",
)

COMPARATIVE_PATTERNS = (
    r"\b(?:outperforms?|surpasses?|exceeds?|beats?)\b",
    r"\b(?:better\s+than|superior\s+to|faster\s+than)\b",
    r"\b(?:state.of.the.art|SOTA|best\s+result|first\s+to)\b",
    r"\b(?:compared?\s+(?:to|with)|in\s+contrast\s+to)\b",
)

METHODOLOGICAL_PATTERNS = (
    r"\b(?:we\s+(?:propose|introduce|present|develop|design|create))\b",
    r"\b(?:our\s+(?:approach|method|framework|model|system|architecture))\b",
    r"\b(?:novel|new\s+(?:approach|method|technique|algorithm))\b",
)

DEFINITIONAL_PATTERNS = (
    r"\b(?:is\s+defined\s+as|can\s+be\s+defined\s+as|is\s+a\s+measure\s+of)\b",
    r"\b(?:refers?\s+to|denotes?|we\s+call|we\s+term|we\s+denote)\b",
)

_LOW_VALUE_SECTIONS = {"acknowledgments", "acknowledgements", "appendix", "supplementary"}


def _classify_claim(text: str) -> str:
    """Classify a claim into one of: numerical, methodological, comparative, definitional, speculative."""
    if any(re.search(p, text, re.IGNORECASE) for p in NUMERICAL_PATTERNS):
        return "numerical"
    if any(re.search(p, text, re.IGNORECASE) for p in COMPARATIVE_PATTERNS):
        return "comparative"
    if any(re.search(p, text, re.IGNORECASE) for p in METHODOLOGICAL_PATTERNS):
        return "methodological"
    if any(re.search(p, text, re.IGNORECASE) for p in DEFINITIONAL_PATTERNS):
        return "definitional"
    return "speculative"


def _verifiability(text: str, claim_type: str) -> str:
    """Estimate how checkable a claim is."""
    if claim_type == "numerical":
        # Numerical claims are the most verifiable
        if any(re.search(p, text, re.IGNORECASE) for p in NUMERICAL_PATTERNS):
            return "high"
    if claim_type == "comparative":
        return "medium"
    if claim_type == "methodological":
        return "medium"
    if claim_type == "speculative":
        return "low"
    return "low"


def _find_section(text: str, position: int) -> str:
    """Try to determine which section a claim belongs to."""
    before = text[max(0, position - 2000) : position]
    section_match = re.search(
        r"(?:^|\n)\s*#*\s*(\d+\.?\s*)?([A-Z][A-Za-z\s\-]{2,60})\s*\n",
        before,
    )
    if section_match:
        section = section_match.group(2).strip().lower()
        if section not in _LOW_VALUE_SECTIONS:
            return section
    return "unknown"


def extract_claims(text: str, min_claim_length: int = 30, max_claims: int = 80) -> list[dict[str, Any]]:
    """Extract structured claims from paper text."""
    claims: list[dict[str, Any]] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if len(sentence) < min_claim_length:
            continue
        if len(sentence) > 300:
            continue

        claim_type = _classify_claim(sentence)

        # Skip sentences that don't look like claims
        if claim_type == "speculative" and len(claims) > 20:
            continue

        claims.append({
            "claim_id": f"claim_{i:04d}",
            "claim_text": sentence,
            "claim_type": claim_type,
            "verifiability": _verifiability(sentence, claim_type),
            "location": _find_section(text, text.find(sentence[:50]) if sentence[:50] in text else 0),
            "evidence_in_paper": "",
        })

        if len(claims) >= max_claims:
            break

    return claims


def extract_from_file(filepath: str, max_claims: int = 80) -> dict[str, Any]:
    """Extract claims from a text file."""
    path = Path(filepath)
    if not path.exists():
        return {"error": f"File not found: {filepath}"}

    raw = path.read_text(encoding="utf-8", errors="replace")
    text = _clean_text(raw)
    claims = extract_claims(text, max_claims=max_claims)

    return {
        "source_file": str(path),
        "text_length": len(text),
        "claim_count": len(claims),
        "claims": claims,
        "claim_type_distribution": _distribution(claims),
    }


def _clean_text(text: str) -> str:
    """Clean and normalise paper text."""
    # Remove excessive whitespace
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    # Remove reference markers like [1], [12,34], [5-7]
    text = re.sub(r"\[\d+(?:[,;\s]*\d+)*\]", "", text)
    return text.strip()


def _distribution(claims: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for c in claims:
        t = c.get("claim_type", "unknown")
        dist[t] = dist.get(t, 0) + 1
    return dist


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract structured, verifiable claims from a paper",
    )
    parser.add_argument(
        "input",
        help="Path to paper text file (plain text from PDF/HTML extraction)",
    )
    parser.add_argument(
        "--max-claims",
        type=int,
        default=80,
        help="Maximum number of claims to extract (default: 80)",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--text",
        default="",
        help="Direct text input instead of a file path",
    )
    args = parser.parse_args()

    if args.text:
        text = _clean_text(args.text)
        claims = extract_claims(text, max_claims=args.max_claims)
        result = {
            "text_length": len(text),
            "claim_count": len(claims),
            "claims": claims,
            "claim_type_distribution": _distribution(claims),
        }
    else:
        result = extract_from_file(args.input, max_claims=args.max_claims)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)


if __name__ == "__main__":
    main()
