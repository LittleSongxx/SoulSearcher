from __future__ import annotations

import re
from typing import Any

_STRUCTURAL_TITLE_TERMS = (
    "architecture",
    "security",
    "controls",
    "evaluation",
    "deployment",
    "tradeoffs",
    "trade-offs",
    "governance",
    "compliance",
    "overview",
    "summary",
    "recommendation",
    "recommendations",
    "conclusion",
    "contents",
    "架构",
    "安全",
    "控制",
    "评估",
    "部署",
    "权衡",
    "治理",
    "合规",
    "概览",
    "摘要",
    "建议",
    "结论",
    "模式",
    "场景",
    "平台",
    "目录",
)

_SENTENCE_END_RE = re.compile(r"[。！？.!?][\]）)]*$")
_INTERNAL_ANCHOR_RE = re.compile(r"\[[^\]]{1,160}\]\(#[^)]+\)")
_TABLE_SEPARATOR_RE = re.compile(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?")
_TRAILING_CITATION_RE = re.compile(r"(?:\s*\[(?:S?\d+(?:-\d+)?)(?:\s*[,，;；]\s*S?\d+(?:-\d+)?)*\])+\s*$")


def _strip_list_marker(value: str) -> str:
    return re.sub(r"^(?:[-*+>]+\s*)?(?:\d+(?:\.\d+)*[.)、]?\s*)?", "", value).strip()


def _rough_word_count(value: str) -> int:
    latin = re.findall(r"[a-z0-9]+", value.lower())
    cjk = re.findall(r"[\u4e00-\u9fff]+", value)
    return len([token for token in latin if len(token) > 1]) + len(cjk)


def is_structural_text(raw: Any, text: Any = None) -> bool:
    raw_value = str(raw or "").strip()
    value = str(text if text is not None else raw_value).strip()
    if not value:
        return True
    if raw_value.startswith("#"):
        return True
    if raw_value.startswith("|") and raw_value.endswith("|"):
        return True
    if _TABLE_SEPARATOR_RE.fullmatch(value):
        return True
    if re.fullmatch(r"[-:|=\s]+", value):
        return True

    without_bullet = re.sub(r"^[#>\-*+\s]+", "", value).strip()
    clean = _strip_list_marker(without_bullet)
    if not clean:
        return True
    if _INTERNAL_ANCHOR_RE.fullmatch(clean):
        return True
    if _INTERNAL_ANCHOR_RE.fullmatch(value):
        return True
    clean = _TRAILING_CITATION_RE.sub("", clean).strip()

    lowered = clean.lower()
    has_sentence_end = bool(_SENTENCE_END_RE.search(clean))
    if not has_sentence_end and re.match(r"^\d+(?:\.\d+){1,6}(?:[\.、\s:：-]+|$)", without_bullet):
        return True
    if not has_sentence_end and re.match(r"^(?:第[一二三四五六七八九十百千万\d]+[章节部分]|[一二三四五六七八九十]+[、.])", clean):
        return True
    if not has_sentence_end and re.match(r"^(?:table of contents|contents|目录|目錄)\b", lowered):
        return True

    has_structural_term = any(term in lowered for term in _STRUCTURAL_TITLE_TERMS)
    word_count = _rough_word_count(clean)
    if not has_sentence_end and has_structural_term and word_count <= 12 and len(clean) <= 90:
        return True
    if not has_sentence_end and re.search(r"[\u4e00-\u9fff]", clean) and word_count <= 4 and len(clean) <= 40:
        return True
    return False
