"""
Deterministic, rule-based claim verifier.

This module implements the *first* of two channels used to check whether a
claim extracted from the draft report is supported by the collected evidence.
It is intentionally **rule-based** — it does not load any Natural Language
Inference (NLI) model. Verification combines token-overlap, numeric and unit
matching, alias detection, and negation / trend conflict detection to assign
each claim a status of ``verified`` / ``contradicted`` / ``unsupported``.

The complementary heuristic channel and the opt-in LLM-judge override live in
``agent.workflows.semantic_claim_verifier``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from agent.workflows.source_registry import SourceRegistry
from agent.workflows.structural_text import is_structural_text

_CLAIM_MARKERS = (
    "research",
    "study",
    "report",
    "data",
    "according to",
    "shows",
    "found",
    "研究",
    "报告",
    "数据显示",
    "统计",
)

_META_CLAIM_PATTERNS = (
    r"^(?:本报告|本文|本研究|本分析)(?:将|旨在|试图|基于|重点|主要)",
    r"^以下(?:将|会)",
    r"^(?:this|the) (?:report|paper|analysis|study) (?:will|aims? to|focuses? on|examines?)",
    r"^we (?:will|aim to|analy[sz]e|examine)",
)

_NEGATION_MARKERS = (
    "not",
    "no",
    "never",
    "without",
    "didn't",
    "doesn't",
    "isn't",
    "wasn't",
    "不是",
    "并非",
    "没有",
    "未",
    "无",
)

_UP_MARKERS = (
    "increase",
    "increased",
    "grow",
    "growth",
    "up",
    "rise",
    "rose",
    "增长",
    "上升",
)
_DOWN_MARKERS = (
    "decrease",
    "decreased",
    "decline",
    "down",
    "fell",
    "drop",
    "下降",
    "减少",
)

_STOPWORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "into",
    "were",
    "was",
    "are",
    "for",
    "has",
    "have",
    "had",
    "will",
    "about",
    "在",
    "是",
    "了",
    "和",
    "与",
    "对",
    "将",
    "及",
}

_MONTH_NUMBERS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _num_key(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(round(value, 4)).rstrip("0").rstrip(".").replace(".", "_")


def _numeric_alias_tokens(text: str) -> set[str]:
    value = text or ""
    lower = value.lower()
    tokens: set[str] = set()

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per\s+cent)", lower):
        tokens.add(f"pct_{_num_key(float(match.group(1)))}")

    for match in re.finditer(
        r"(?:€|eur\s*)(\d+(?:\.\d+)?)\s*(million|billion|m|bn)?", lower
    ):
        amount = float(match.group(1))
        unit = match.group(2) or ""
        if unit in {"billion", "bn"}:
            amount *= 1000
        tokens.add(f"euro_million_{_num_key(amount)}")

    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(million|billion|m|bn)\s*(?:euros?|eur)", lower
    ):
        amount = float(match.group(1))
        unit = match.group(2)
        if unit in {"billion", "bn"}:
            amount *= 1000
        tokens.add(f"euro_million_{_num_key(amount)}")

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*万\s*欧元", value):
        tokens.add(f"euro_million_{_num_key(float(match.group(1)) / 100)}")

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*亿\s*欧元", value):
        tokens.add(f"euro_million_{_num_key(float(match.group(1)) * 100)}")

    for match in re.finditer(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        tokens.add(f"date_{year:04d}_{month:02d}_{day:02d}")
        tokens.add(f"date_{year:04d}_{month:02d}")

    for match in re.finditer(r"(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?", value):
        year, month = int(match.group(1)), int(match.group(2))
        day = match.group(3)
        tokens.add(f"date_{year:04d}_{month:02d}")
        if day:
            tokens.add(f"date_{year:04d}_{month:02d}_{int(day):02d}")

    month_pattern = "|".join(_MONTH_NUMBERS)
    for match in re.finditer(
        rf"\b(\d{{1,2}})\s+({month_pattern})\s*,?\s*(\d{{4}})\b", lower
    ):
        day, month, year = (
            int(match.group(1)),
            _MONTH_NUMBERS[match.group(2)],
            int(match.group(3)),
        )
        tokens.add(f"date_{year:04d}_{month:02d}_{day:02d}")
        tokens.add(f"date_{year:04d}_{month:02d}")

    for match in re.finditer(
        rf"\b({month_pattern})\s+(\d{{1,2}}),?\s*(\d{{4}})\b", lower
    ):
        month, day, year = (
            _MONTH_NUMBERS[match.group(1)],
            int(match.group(2)),
            int(match.group(3)),
        )
        tokens.add(f"date_{year:04d}_{month:02d}_{day:02d}")
        tokens.add(f"date_{year:04d}_{month:02d}")

    for match in re.finditer(rf"\b({month_pattern})\s+(\d{{4}})\b", lower):
        month, year = _MONTH_NUMBERS[match.group(1)], int(match.group(2))
        tokens.add(f"date_{year:04d}_{month:02d}")

    for match in re.finditer(r"articles?\s*(\d+)\s*(?:to|-|–|—|至)\s*(\d+)", lower):
        tokens.add(f"article_range_{match.group(1)}_{match.group(2)}")
    for match in re.finditer(r"articles?\s*(\d+)\s*至\s*(\d+)", lower):
        tokens.add(f"article_range_{match.group(1)}_{match.group(2)}")

    return tokens


_SEMANTIC_ALIAS_PATTERNS = {
    "post_market_monitoring": (
        r"post[-\s]?market monitoring",
        r"后市场监控",
    ),
    "serious_incident": (
        r"serious incidents?",
        r"严重事件",
    ),
    "corrective_action": (
        r"corrective (?:actions?|measures?)",
        r"纠正措施",
    ),
    "market_surveillance_authority": (
        r"market surveillance authorit(?:y|ies)",
        r"市场监管机构",
    ),
    "cooperate_authorities": (
        r"cooperat\w+ with (?:market surveillance )?(?:authorit(?:y|ies)|regulators?)",
        r"与市场监管机构合作",
        r"监管机构合作",
    ),
    "provider_obligation": (
        r"providers? (?:are )?required",
        r"提供商.*(?:要求|义务)",
        r"供应商.*(?:要求|义务)",
    ),
    "advanced_packaging": (
        r"advanced packaging",
        r"先进封装",
        r"封装环节",
    ),
    "wafer_production": (
        r"wafer production",
        r"silicon fabrication",
        r"晶圆制造",
    ),
    "industry_bottleneck": (
        r"(?:true|primary|industry )?bottleneck",
        r"瓶颈",
    ),
    "chip_export_policy": (
        r"chip exports?",
        r"export controls?",
        r"出口政策",
        r"出口管制",
    ),
    "ripple_effect": (
        r"ripple effect",
        r"knock-on effects?",
        r"连锁效应",
    ),
    "allies_planning": (
        r"allies.*(?:industrial|ai).*planning",
        r"盟国.*(?:工业|ai).*计划",
        r"盟友.*(?:工业|ai).*规划",
    ),
    "washington": (
        r"washington",
        r"华盛顿",
    ),
    "netherlands": (
        r"netherlands",
        r"荷兰",
    ),
    "taiwan": (
        r"taiwan",
        r"台湾",
    ),
    "japan": (
        r"japan",
        r"日本",
    ),
    "interdependent_supply_chain": (
        r"interdependent supply chain",
        r"相互依赖供应链",
    ),
    "neutral_atom": (
        r"neutral atoms?",
        r"中性原子",
    ),
    "quantum_memory": (
        r"quantum memor(?:y|ies)",
        r"量子存储器",
    ),
    "quantum_error_correction": (
        r"quantum error correction",
        r"error-correcting codes?",
        r"量子纠错",
        r"量子错误校正",
    ),
    "quera_led_study": (
        r"quera[-\s]?led study",
        r"quera.*(?:研究|领导)",
    ),
    "quantum_practical_hardware": (
        r"practical hardware",
        r"实用硬件",
    ),
}


def _semantic_alias_tokens(text: str) -> set[str]:
    value = text or ""
    lower = value.lower()
    tokens: set[str] = set()
    for token, patterns in _SEMANTIC_ALIAS_PATTERNS.items():
        if any(re.search(pattern, lower, flags=re.IGNORECASE) for pattern in patterns):
            tokens.add(token)
    return tokens


class ClaimStatus(str, Enum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


@dataclass
class ClaimCheck:
    claim: str
    status: ClaimStatus
    evidence_urls: list[str] = field(default_factory=list)
    evidence_passages: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0
    notes: str = ""


class ClaimVerifier:
    """Deterministic claim-to-evidence matcher."""

    def __init__(self, min_overlap_tokens: int = 2, *, max_evidence_per_claim: int = 5):
        self.min_overlap_tokens = max(1, int(min_overlap_tokens))
        self.max_evidence_per_claim = max(1, int(max_evidence_per_claim))

    def _is_structural_heading_candidate(self, raw: str, text: str) -> bool:
        return is_structural_text(raw, text)

    def extract_claims(self, report: str, max_claims: int = 10) -> list[str]:
        if not report:
            return []

        body = re.split(
            r"^##\s*(?:参考来源|References)\b",
            report,
            maxsplit=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )[0]
        candidates = re.split(r"(?<=[。！？.!?])\s+|\n+", body)
        claims: list[str] = []
        seen: set[str] = set()

        for sentence in candidates:
            raw_sentence = sentence.strip()
            text = re.sub(
                r"\[(?:S?\d+(?:-\d+)?)(?:\s*[,，;；]\s*S?\d+(?:-\d+)?)*\]", "", sentence
            )
            text = re.sub(r"^[#>\-\*\s]+", "", text).strip()
            if self._is_structural_heading_candidate(raw_sentence, text):
                continue
            if len(text) < 20:
                continue
            lower = text.lower()
            if any(
                re.search(pattern, lower, flags=re.IGNORECASE)
                for pattern in _META_CLAIM_PATTERNS
            ):
                continue
            has_signal = any(marker in lower for marker in _CLAIM_MARKERS) or bool(
                re.search(r"\d{2,4}|\d+%|\d+\.\d+", text)
            )
            if not has_signal:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            claims.append(text)
            if len(claims) >= max_claims:
                break

        return claims

    def verify_report(
        self,
        report: str,
        scraped_content: list[dict[str, Any]],
        max_claims: int = 10,
        passages: Optional[list[dict[str, Any]]] = None,
    ) -> list[ClaimCheck]:
        claims = self.extract_claims(report, max_claims=max_claims)
        if not claims:
            return []
        evidence = self._extract_evidence(scraped_content, passages=passages)
        return [self.verify_claim(claim, evidence) for claim in claims]

    def verify_claim(self, claim: str, evidence: list[dict[str, Any]]) -> ClaimCheck:
        claim_tokens = self._tokenize(claim)
        if not claim_tokens:
            return ClaimCheck(claim=claim, status=ClaimStatus.UNSUPPORTED)

        supported: list[tuple[int, str, dict[str, Any]]] = []
        contradicted: list[tuple[int, str, dict[str, Any]]] = []
        best_overlap = 0

        for item in evidence:
            url = str(item.get("url") or "").strip() or "unknown"
            text = str(item.get("text") or "").strip()
            evidence_tokens = self._tokenize(text)
            overlap = len(claim_tokens & evidence_tokens)
            if overlap < self.min_overlap_tokens:
                continue

            best_overlap = max(best_overlap, overlap)
            passage_payload: dict[str, Any] = {
                "url": url,
            }
            snippet_hash = str(item.get("snippet_hash") or "").strip()
            if snippet_hash:
                passage_payload["snippet_hash"] = snippet_hash
            quote = str(item.get("quote") or "").strip()
            if quote:
                passage_payload["quote"] = quote
            heading_path = item.get("heading_path")
            if isinstance(heading_path, list) and all(
                isinstance(p, str) for p in heading_path
            ):
                passage_payload["heading_path"] = heading_path

            if self._is_contradiction(claim, text):
                contradicted.append((overlap, url, passage_payload))
            else:
                supported.append((overlap, url, passage_payload))

        contradicted.sort(key=lambda row: -row[0])
        supported.sort(key=lambda row: -row[0])
        limit = self.max_evidence_per_claim

        best_contradiction = contradicted[0][0] if contradicted else 0
        best_support = supported[0][0] if supported else 0
        if (
            contradicted
            and best_contradiction >= max(3, self.min_overlap_tokens)
            and best_contradiction > best_support
        ):
            urls = list(
                dict.fromkeys(
                    [u for _o, u, _p in contradicted] + [u for _o, u, _p in supported]
                )
            )
            evidence_passages = [p for _o, _u, p in (contradicted + supported)][:limit]
            return ClaimCheck(
                claim=claim,
                status=ClaimStatus.CONTRADICTED,
                evidence_urls=urls[:limit],
                evidence_passages=evidence_passages,
                score=float(best_overlap),
                notes="conflicting evidence found",
            )

        if supported:
            return ClaimCheck(
                claim=claim,
                status=ClaimStatus.VERIFIED,
                evidence_urls=list(dict.fromkeys([u for _o, u, _p in supported]))[
                    :limit
                ],
                evidence_passages=[p for _o, _u, p in supported][:limit],
                score=float(best_overlap),
                notes="supported by evidence",
            )

        return ClaimCheck(
            claim=claim,
            status=ClaimStatus.UNSUPPORTED,
            evidence_urls=[],
            evidence_passages=[],
            score=0.0,
            notes="no matching evidence",
        )

    def _extract_evidence(
        self,
        scraped_content: list[dict[str, Any]],
        *,
        passages: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        source_registry = SourceRegistry()

        if passages:
            for passage in passages:
                if not isinstance(passage, dict):
                    continue
                url = str(passage.get("url") or "").strip()
                if not url:
                    continue
                canonical_url = source_registry.canonicalize_url(url) or url
                text = str(
                    passage.get("text")
                    or passage.get("markdown")
                    or passage.get("content")
                    or passage.get("snippet")
                    or ""
                ).strip()
                if not text:
                    continue
                item: dict[str, Any] = {
                    "url": canonical_url,
                    "text": text,
                }
                snippet_hash = str(passage.get("snippet_hash") or "").strip()
                if snippet_hash:
                    item["snippet_hash"] = snippet_hash
                quote = str(passage.get("quote") or "").strip()
                if quote:
                    item["quote"] = quote
                heading_path = passage.get("heading_path")
                if isinstance(heading_path, list) and all(
                    isinstance(p, str) for p in heading_path
                ):
                    item["heading_path"] = heading_path
                evidence.append(item)

        for item in scraped_content or []:
            for result in item.get("results", []) or []:
                url = str(result.get("url") or "").strip() or "unknown"
                canonical_url = source_registry.canonicalize_url(url) or url
                parts = [
                    result.get("title"),
                    result.get("raw_excerpt"),
                    result.get("content"),
                    result.get("summary"),
                    result.get("snippet"),
                ]
                text = " ".join(
                    str(part).strip() for part in parts if str(part or "").strip()
                )
                if text:
                    evidence.append({"url": canonical_url, "text": text})
        return evidence

    def _tokenize(self, text: str) -> set[str]:
        value = re.sub(
            r"\[(?:S?\d+(?:-\d+)?)(?:\s*[,，;；]\s*S?\d+(?:-\d+)?)*\]",
            " ",
            (text or "").lower(),
        )
        tokens = {
            t
            for t in re.findall(r"[a-z0-9]+", value)
            if len(t) > 1 and t not in _STOPWORDS
        }
        tokens.update(_numeric_alias_tokens(text or ""))
        tokens.update(_semantic_alias_tokens(text or ""))
        for seq in re.findall(r"[\u4e00-\u9fff]{2,}", value):
            tokens.update(seq[i : i + 2] for i in range(0, max(0, len(seq) - 1)))
            if len(seq) <= 6 and seq not in _STOPWORDS:
                tokens.add(seq)
        return tokens

    @staticmethod
    def _marker_in_text(marker: str, text: str) -> bool:
        """Check if *marker* appears in *text* as a whole word.

        CJK markers use plain substring matching (Chinese has no whitespace
        word boundaries).  ASCII markers require ``\\b`` word boundaries to
        avoid false positives like "no" inside "now".
        """
        if re.search(r"[\u4e00-\u9fff]", marker):
            return marker in text
        return bool(re.search(r"(?:^|\W)" + re.escape(marker) + r"(?:$|\W)", text))

    def _has_negation(self, text: str) -> bool:
        lower = (text or "").lower()
        for marker in _NEGATION_MARKERS:
            candidate = lower.replace("未来", "") if marker == "未" else lower
            if self._marker_in_text(marker, candidate):
                return True
        return False

    def _trend_direction(self, text: str) -> int:
        lower = (text or "").lower()
        up = any(self._marker_in_text(m, lower) for m in _UP_MARKERS)
        down = any(self._marker_in_text(m, lower) for m in _DOWN_MARKERS)
        if up and not down:
            return 1
        if down and not up:
            return -1
        return 0

    def _best_evidence_fragment(self, claim: str, evidence: str) -> str:
        claim_tokens = self._tokenize(claim)
        fragments = [
            part.strip()
            for part in re.split(r"(?<=[。！？.!?])\s*|\n+", evidence or "")
            if part and part.strip()
        ]
        if not fragments:
            return evidence or ""
        best_fragment = ""
        best_overlap = 0
        for fragment in fragments:
            overlap = len(claim_tokens & self._tokenize(fragment))
            if overlap > best_overlap:
                best_overlap = overlap
                best_fragment = fragment
        if best_overlap < max(3, self.min_overlap_tokens):
            return ""
        return best_fragment

    def _has_supportive_exception_to_negation(self, claim: str, evidence: str) -> bool:
        if not re.search(
            r"\bnot\b[^.。！？!?]{0,120}\b(?:but|as|rather than|instead of)\b",
            evidence.lower(),
        ):
            return False
        shared = self._tokenize(claim) & self._tokenize(evidence)
        semantic_shared = {token for token in shared if "_" in token}
        return len(semantic_shared) >= 2

    def _is_contradiction(self, claim: str, evidence: str) -> bool:
        evidence = self._best_evidence_fragment(claim, evidence)
        if not evidence:
            return False
        claim_neg = self._has_negation(claim)
        evidence_neg = self._has_negation(evidence)
        if claim_neg != evidence_neg:
            if evidence_neg and self._has_supportive_exception_to_negation(
                claim, evidence
            ):
                return False
            return True

        claim_dir = self._trend_direction(claim)
        evidence_dir = self._trend_direction(evidence)
        if claim_dir != 0 and evidence_dir != 0 and claim_dir != evidence_dir:
            return True

        return False
