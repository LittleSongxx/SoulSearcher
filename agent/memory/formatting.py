from __future__ import annotations

from collections import defaultdict

from agent.memory.models import MemoryRecord, MemoryRelation, MemoryRetrievalResult, MemoryType


def _trim(text: str, max_chars: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 3)].rstrip() + "..."


def _record_line(record: MemoryRecord) -> str:
    label = record.summary or record.content
    evidence = ""
    if record.source_urls:
        evidence = f" sources={', '.join(record.source_urls[:2])}"
    return (
        f"- ({record.type}, confidence={record.confidence:.2f}, "
        f"importance={record.importance:.2f}) {_trim(label, 260)}{evidence}"
    )


def _relation_line(relation: MemoryRelation) -> str:
    return (
        f"- relation: {relation.source_entity_id} {relation.relation} "
        f"{relation.target_entity_id} confidence={relation.confidence:.2f}"
    )


def build_memory_context(
    result: MemoryRetrievalResult,
    *,
    max_tokens: int = 2500,
) -> str:
    """Format retrieved memory as hidden context with explicit usage rules."""
    budget_chars = max(400, int(max_tokens or 2500) * 4)
    by_type: dict[str, list[MemoryRecord]] = defaultdict(list)
    for record in result.records:
        by_type[record.type].append(record)

    sections: list[str] = ["<memory_context>"]
    profile_items = by_type.get(MemoryType.profile.value, []) + by_type.get(
        MemoryType.preference.value, []
    )
    if profile_items:
        sections.append("<user_profile_memory>")
        sections.extend(_record_line(record) for record in profile_items[:6])
        sections.append("</user_profile_memory>")

    research_items = (
        by_type.get(MemoryType.fact.value, [])
        + by_type.get(MemoryType.research_finding.value, [])
        + by_type.get(MemoryType.source.value, [])
        + by_type.get(MemoryType.episode.value, [])
    )
    if research_items:
        sections.append("<relevant_research_memory>")
        sections.extend(_record_line(record) for record in research_items[:10])
        sections.append("</relevant_research_memory>")

    if result.entities or result.relations:
        sections.append("<entity_graph_memory>")
        for entity in result.entities[:8]:
            aliases = f" aliases={', '.join(entity.aliases[:4])}" if entity.aliases else ""
            sections.append(f"- entity: {entity.name} type={entity.type}{aliases}")
        sections.extend(_relation_line(relation) for relation in result.relations[:8])
        sections.append("</entity_graph_memory>")

    procedural_items = by_type.get(MemoryType.procedure.value, [])
    if procedural_items:
        sections.append("<procedural_memory>")
        sections.extend(_record_line(record) for record in procedural_items[:6])
        sections.append("</procedural_memory>")

    sections.append("<memory_usage_rules>")
    sections.append(
        "- Use memory as research leads and personalization only."
    )
    sections.append(
        "- Final report citations must come from the current run evidence ledger."
    )
    sections.append(
        "- Re-verify memory sources before citing."
    )
    sections.append("</memory_usage_rules>")
    sections.append("</memory_context>")

    text = "\n".join(sections)
    if len(text) > budget_chars:
        text = text[:budget_chars].rstrip() + "\n</memory_context>"
    return text
