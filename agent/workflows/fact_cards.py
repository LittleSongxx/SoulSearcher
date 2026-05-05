from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.workflows.source_url_utils import canonicalize_source_url


@dataclass
class FactCard:
    fact_id: str
    claim: str
    source_index: int
    source_url: str
    source_title: str = ""
    evidence_id: str = ""
    quote: str = ""
    source_type: str = ""
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical(value: Any) -> str:
    return canonicalize_source_url(value) or _text(value)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _snippet(item: Dict[str, Any]) -> str:
    for key in ("snippet", "quote", "text", "summary", "content", "raw_excerpt", "markdown"):
        value = _text(item.get(key))
        if value:
            return value
    return ""


def _source_index(sources: Iterable[Dict[str, Any]]) -> Dict[str, Tuple[int, Dict[str, Any]]]:
    mapping: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for idx, source in enumerate(sources or [], 1):
        if not isinstance(source, dict):
            continue
        for key in (source.get("url"), source.get("rawUrl")):
            canonical = _canonical(key)
            if canonical and canonical not in mapping:
                mapping[canonical] = (idx, source)
    return mapping


def _claim_candidate(text: str) -> str:
    value = _text(text)
    if not value:
        return ""
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])|(?<=[.!?])\s+", value) if part.strip()]
    candidates = parts or [value]
    ranked = sorted(candidates, key=lambda item: (_claim_signal_score(item), len(item)), reverse=True)
    selected = ranked[0] if ranked else value
    return selected[:320]


def _claim_signal_score(text: str) -> int:
    markers = (
        r"\d{4}",
        r"\d+%",
        r"\d+\.\d+",
        r"according to|report|study|data|shows|found|announced|official|benchmark",
        r"报告|研究|数据显示|统计|公告|监管|增长|下降|发布|官方|基准|显示",
    )
    return sum(1 for marker in markers if re.search(marker, text, flags=re.IGNORECASE))


def _confidence(item: Dict[str, Any], quote: str, source: Dict[str, Any]) -> Tuple[float, List[str]]:
    score = 0.25
    reasons: List[str] = []
    source_type = _text(item.get("source_type")).lower()
    if source_type == "passage":
        score += 0.3
        reasons.append("passage")
    elif source_type == "fetched_page":
        score += 0.22
        reasons.append("fetched_page")
    elif source_type in {"web", "legacy_source", "tree_finding"}:
        score += 0.12
        reasons.append(source_type or "web")
    if item.get("content_ref") or item.get("snippet_hash") or item.get("id"):
        score += 0.08
        reasons.append("traceable_evidence_id")
    if len(quote) >= 120:
        score += 0.08
        reasons.append("substantial_quote")
    if _claim_signal_score(quote) > 0:
        score += 0.08
        reasons.append("claim_signal")
    reliability = source.get("reliability_score") if isinstance(source, dict) else None
    try:
        if reliability is not None:
            score += min(0.12, max(0.0, float(reliability)) * 0.12)
            reasons.append("source_reliability")
    except (TypeError, ValueError):
        pass
    return round(max(0.0, min(1.0, score)), 4), reasons


def build_fact_cards(
    *,
    evidence_items: Iterable[Dict[str, Any]],
    sources: Iterable[Dict[str, Any]],
    max_cards: int = 80,
    min_quote_chars: int = 40,
) -> List[Dict[str, Any]]:
    source_map = _source_index(sources)
    rows: List[Tuple[float, int, FactCard]] = []
    seen = set()
    for idx, item in enumerate(evidence_items or []):
        if not isinstance(item, dict):
            continue
        quote = _snippet(item)
        if len(quote) < max(1, int(min_quote_chars or 1)):
            continue
        source_url = _canonical(item.get("url"))
        if not source_url or source_url not in source_map:
            continue
        source_idx, source = source_map[source_url]
        claim = _claim_candidate(quote)
        if not claim:
            continue
        evidence_id = _text(item.get("id") or item.get("evidence_id") or item.get("content_ref") or item.get("snippet_hash"))
        key = (source_idx, evidence_id, claim[:160].lower())
        if key in seen:
            continue
        seen.add(key)
        confidence, reasons = _confidence(item, quote, source)
        title = _text(source.get("title") or source.get("name") or item.get("title") or item.get("page_title"))
        card = FactCard(
            fact_id=_stable_id("fact", source_idx, evidence_id, claim[:180]),
            claim=claim,
            source_index=source_idx,
            source_url=source_url,
            source_title=title,
            evidence_id=evidence_id,
            quote=quote[:520],
            source_type=_text(item.get("source_type")),
            confidence=confidence,
            reasons=reasons,
        )
        rows.append((confidence, idx, card))
    rows.sort(key=lambda row: (-row[0], row[1]))
    selected = rows if max_cards <= 0 else rows[: max(1, int(max_cards))]
    selected.sort(key=lambda row: row[1])
    return [card.to_dict() for _score, _idx, card in selected]


def format_fact_cards_for_writer(fact_cards: Iterable[Dict[str, Any]], *, max_cards: int = 40) -> str:
    cards = [card for card in fact_cards or [] if isinstance(card, dict)]
    if not cards:
        return ""
    lines = [
        "# Fact Cards / 可引用事实卡",
        "写作时只能把下列 fact_card 中有 source_index 的事实写成确定事实；每个事实性句子必须在句末使用对应 [source_index]。不要原样输出本区标题。",
    ]
    for card in cards[: max(1, int(max_cards or 1))]:
        ref = card.get("source_index")
        claim = _text(card.get("claim"))
        quote = _text(card.get("quote"))
        evidence_id = _text(card.get("evidence_id"))
        confidence = card.get("confidence")
        line = f"- {card.get('fact_id')}: ref=[{ref}]; confidence={confidence}; claim={claim}"
        if evidence_id:
            line += f"; evidence_id={evidence_id}"
        if quote and quote != claim:
            line += f"; quote={quote[:260]}"
        lines.append(line)
    return "\n".join(lines).strip()


def match_claim_to_fact_card(claim: str, fact_cards: Iterable[Dict[str, Any]], *, min_overlap: float = 0.28) -> Optional[Dict[str, Any]]:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return None
    best: Tuple[float, Dict[str, Any] | None] = (0.0, None)
    for card in fact_cards or []:
        if not isinstance(card, dict):
            continue
        text = " ".join([_text(card.get("claim")), _text(card.get("quote"))])
        tokens = _tokens(text)
        if not tokens:
            continue
        overlap = len(claim_tokens & tokens) / max(1, min(len(claim_tokens), len(tokens)))
        try:
            confidence = float(card.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        score = overlap + confidence * 0.08
        if score > best[0]:
            best = (score, card)
    if best[1] is not None and best[0] >= float(min_overlap):
        return best[1]
    return None


def repair_citations_with_fact_cards(report: str, missing_claims: Iterable[str], fact_cards: Iterable[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    missing = [_text(claim) for claim in missing_claims or [] if _text(claim)]
    if not report or not missing:
        return report, {"enabled": True, "repaired_count": 0, "unmatched_count": 0, "method": "fact_card"}
    replacements: Dict[str, str] = {}
    unmatched = 0
    for claim in missing:
        card = match_claim_to_fact_card(claim, fact_cards)
        ref = card.get("source_index") if isinstance(card, dict) else None
        if isinstance(ref, int) and ref > 0:
            replacements[claim] = _append_ref_to_sentence(claim, ref) if not re.search(r"\[(?:S?\d+)\]", claim) else claim
        else:
            unmatched += 1
    if not replacements:
        return report, {"enabled": True, "repaired_count": 0, "unmatched_count": unmatched, "method": "fact_card"}
    revised = report
    for claim, replacement in replacements.items():
        if claim in revised:
            revised = revised.replace(claim, replacement, 1)
            continue
        key = _normalize_for_match(claim)
        for sentence in _split_sentences(revised):
            if _normalize_for_match(sentence) == key:
                revised = revised.replace(sentence, replacement, 1)
                break
    return revised, {
        "enabled": True,
        "repaired_count": sum(1 for claim in replacements if f"{claim} [" in revised or replacements[claim] in revised),
        "unmatched_count": unmatched,
        "method": "fact_card",
    }


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9\u4e00-\u9fff]+", str(text or "").lower()) if len(token) > 1}


def _normalize_for_match(text: str) -> str:
    value = re.sub(r"\[(?:S?\d+)\]", "", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _append_ref_to_sentence(sentence: str, ref: int) -> str:
    text = str(sentence or "").strip()
    if not text:
        return text
    match = re.search(r"([。！？!?]|[.!?])(\s*)$", text)
    if match:
        return f"{text[:match.start()].rstrip()} [{ref}]{match.group(1)}"
    return f"{text} [{ref}]"


def _split_sentences(text: str) -> List[str]:
    sentences: List[str] = []
    for line in str(text or "").splitlines():
        sentences.extend(part.strip() for part in re.findall(r".+?(?:[。！？!?]|[.!?](?=\s|$))|.+$", line) if part.strip())
    return sentences
