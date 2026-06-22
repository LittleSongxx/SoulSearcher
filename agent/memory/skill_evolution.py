from __future__ import annotations

import re
from pathlib import Path

from agent.memory.models import MemoryRecord, MemoryType, SkillEvolutionProposal, SkillEvolutionStatus


def _safe_skill_name(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", str(text or "").casefold())
    if not words:
        return "memory-learned-research"
    name = "-".join(words[:5]).strip("-")
    return name[:64].strip("-") or "memory-learned-research"


def build_skill_markdown(skill_name: str, procedural_records: list[MemoryRecord]) -> str:
    bullets = []
    seen = set()
    for record in procedural_records:
        text = " ".join(record.content.split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        bullets.append(f"- {text[:500]}")
    body = "\n".join(bullets[:8]) or "- Preserve source-backed evidence before final synthesis."
    return (
        "---\n"
        f"name: {skill_name}\n"
        "description: Auto-evolved research procedure distilled from successful Weaver DeepResearch runs.\n"
        "allowed-tools:\n"
        "  - tavily_search\n"
        "  - fallback_search\n"
        "  - deep_read\n"
        "  - read_skill_guide\n"
        "---\n\n"
        "# Auto-Evolved Research Procedure\n\n"
        "Use this skill when a research task resembles prior successful or corrected runs.\n\n"
        "## Procedure\n\n"
        f"{body}\n\n"
        "## Evidence Discipline\n\n"
        "- Treat remembered procedures as guidance only.\n"
        "- Verify facts with current-run sources before citing.\n"
    )


async def maybe_evolve_skill(
    *,
    store,
    user_id: str,
    procedural_records: list[MemoryRecord],
    min_support: int,
    enabled: bool,
) -> SkillEvolutionProposal | None:
    if not enabled or len(procedural_records) < max(1, int(min_support or 1)):
        return None
    seed = procedural_records[0].summary or procedural_records[0].content
    skill_name = _safe_skill_name(seed)
    content = build_skill_markdown(skill_name, procedural_records)
    avg_confidence = sum(record.confidence for record in procedural_records) / len(procedural_records)
    proposal = SkillEvolutionProposal(
        user_id=user_id,
        skill_name=skill_name,
        content=content,
        rationale=(
            "Generated from repeated procedural memory across "
            f"{len(procedural_records)} DeepResearch run(s)."
        ),
        support_count=len(procedural_records),
        confidence=avg_confidence,
    )

    try:
        from agent.skills.security_scanner import scan_skill_content
        from agent.skills.storage import get_or_new_skill_storage
        from agent.skills.validation import _validate_skill_frontmatter

        scan = await scan_skill_content(
            content,
            executable=False,
            location=f"{skill_name}/SKILL.md",
        )
        if scan.decision == "block":
            proposal.status = SkillEvolutionStatus.failed.value
            proposal.validation_status = f"security_blocked: {scan.reason}"
            return store.add_skill_evolution(proposal)

        storage = get_or_new_skill_storage()
        skill_dir = storage.get_custom_skill_dir(skill_name)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        previous = skill_file.read_text(encoding="utf-8") if skill_file.exists() else ""
        storage.write_custom_skill(skill_name, "SKILL.md", content)
        valid, message, _name = _validate_skill_frontmatter(Path(skill_dir))
        if not valid:
            if previous:
                storage.write_custom_skill(skill_name, "SKILL.md", previous)
            else:
                skill_file.unlink(missing_ok=True)
            proposal.status = SkillEvolutionStatus.failed.value
            proposal.validation_status = f"validation_failed: {message}"
            return store.add_skill_evolution(proposal)
        try:
            storage.append_history(
                skill_name,
                {
                    "source": "memory_skill_evolution",
                    "prev_content": previous,
                    "support_count": len(procedural_records),
                },
            )
        except Exception:
            pass
        proposal.status = SkillEvolutionStatus.applied.value
        proposal.validation_status = "passed"
        proposal.applied_path = str(skill_file)
    except Exception as exc:
        proposal.status = SkillEvolutionStatus.failed.value
        proposal.validation_status = f"error: {exc}"

    return store.add_skill_evolution(proposal)
