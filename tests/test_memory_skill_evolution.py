from __future__ import annotations

import asyncio


def _procedural_records(count: int = 3):
    from agent.memory.models import MemoryRecord, MemoryScope, MemoryType

    return [
        MemoryRecord(
            user_id="u1",
            scope=MemoryScope.research.value,
            type=MemoryType.procedure.value,
            content=(
                "For similar DeepResearch runs, preserve source-backed evidence "
                "passages before drafting the final report."
            ),
            summary="source-backed evidence procedure",
            confidence=0.9,
        )
        for _ in range(count)
    ]


def test_skill_evolution_applies_valid_custom_skill(tmp_path, monkeypatch):
    from agent.memory import InMemoryMemoryStore
    from agent.memory.skill_evolution import maybe_evolve_skill
    from agent.skills.security_scanner import ScanResult
    from agent.skills.storage.local_skill_storage import LocalSkillStorage

    async def allow_scan(*_args, **_kwargs):
        return ScanResult("allow", "ok")

    storage = LocalSkillStorage(host_path=str(tmp_path))
    monkeypatch.setattr("agent.skills.security_scanner.scan_skill_content", allow_scan)
    monkeypatch.setattr("agent.skills.storage.get_or_new_skill_storage", lambda: storage)

    store = InMemoryMemoryStore()
    proposal = asyncio.run(
        maybe_evolve_skill(
            store=store,
            user_id="u1",
            procedural_records=_procedural_records(3),
            min_support=3,
            enabled=True,
        )
    )

    assert proposal is not None
    assert proposal.status == "applied"
    assert proposal.validation_status == "passed"
    assert proposal.applied_path
    assert "/custom/" in proposal.applied_path
    assert (tmp_path / "custom" / proposal.skill_name / "SKILL.md").exists()
    assert store.list_skill_evolution(user_id="u1")[0].id == proposal.id


def test_skill_evolution_security_block_does_not_write_skill(tmp_path, monkeypatch):
    from agent.memory import InMemoryMemoryStore
    from agent.memory.skill_evolution import maybe_evolve_skill
    from agent.skills.security_scanner import ScanResult
    from agent.skills.storage.local_skill_storage import LocalSkillStorage

    async def block_scan(*_args, **_kwargs):
        return ScanResult("block", "unsafe")

    storage = LocalSkillStorage(host_path=str(tmp_path))
    monkeypatch.setattr("agent.skills.security_scanner.scan_skill_content", block_scan)
    monkeypatch.setattr("agent.skills.storage.get_or_new_skill_storage", lambda: storage)

    store = InMemoryMemoryStore()
    proposal = asyncio.run(
        maybe_evolve_skill(
            store=store,
            user_id="u1",
            procedural_records=_procedural_records(3),
            min_support=3,
            enabled=True,
        )
    )

    assert proposal is not None
    assert proposal.status == "failed"
    assert proposal.validation_status.startswith("security_blocked")
    assert not (tmp_path / "custom").exists()


def test_skill_evolution_respects_min_support():
    from agent.memory import InMemoryMemoryStore
    from agent.memory.skill_evolution import maybe_evolve_skill

    proposal = asyncio.run(
        maybe_evolve_skill(
            store=InMemoryMemoryStore(),
            user_id="u1",
            procedural_records=_procedural_records(2),
            min_support=3,
            enabled=True,
        )
    )

    assert proposal is None
