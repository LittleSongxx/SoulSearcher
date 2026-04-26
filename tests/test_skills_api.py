import sys
import textwrap
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from common import skills_loader


def _write_skill(path: Path, frontmatter: str, body: str = '# Role\n\nYou are a skill.') -> None:
    path.write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{textwrap.dedent(body).strip()}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_skills_api_admin_views_and_toggle(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    state_dir = tmp_path / "state"

    _write_skill(
        skills_dir / "valid.md",
        """
        id: api-valid
        name: API Valid Skill
        description: A valid skill visible by default
        category: tool
        mode: agent
        tools: []
        permissions:
          network: false
          filesystem: none
          shell: false
          browser: false
          sandbox: false
          external_apis: false
        """,
    )
    _write_skill(
        skills_dir / "invalid.md",
        """
        id: api-invalid
        name: API Invalid Skill
        category: tool
        mode: invalid_mode
        tools: []
        permissions:
          network: false
          filesystem: none
          shell: false
          browser: false
          sandbox: false
          external_apis: false
        """,
    )
    _write_skill(
        skills_dir / "disabled.md",
        """
        id: api-disabled
        name: API Disabled Skill
        description: Hidden until enabled
        category: tool
        mode: agent
        status: disabled
        tools: []
        permissions:
          network: false
          filesystem: none
          shell: false
          browser: false
          sandbox: false
          external_apis: false
        """,
    )

    monkeypatch.setattr(skills_loader, "_default_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(
        skills_loader,
        "_state_paths",
        lambda project_root=None: skills_loader.SkillsStatePaths(
            root=state_dir,
            file=state_dir / "skills_state.json",
        ),
    )
    skills_loader.reload_skill_registry()

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        default_resp = await ac.get("/api/skills")
        assert default_resp.status_code == 200
        default_skills = default_resp.json()["skills"]
        assert [skill["id"] for skill in default_skills] == ["api-valid"]

        admin_resp = await ac.get(
            "/api/skills",
            params={"include_invalid": "true", "include_disabled": "true"},
        )
        assert admin_resp.status_code == 200
        admin_payload = admin_resp.json()
        assert {skill["id"] for skill in admin_payload["skills"]} == {
            "api-valid",
            "api-invalid",
            "api-disabled",
        }
        assert admin_payload["registry"]["invalid_count"] == 1
        assert admin_payload["registry"]["disabled_count"] == 1

        detail_resp = await ac.get(
            "/api/skills/api-invalid",
            params={"include_invalid": "true"},
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["validation"]["valid"] is False

        disable_resp = await ac.post("/api/skills/api-valid/disable")
        assert disable_resp.status_code == 200
        assert disable_resp.json()["disabled_count"] == 2

        after_disable = await ac.get("/api/skills")
        assert after_disable.status_code == 200
        assert after_disable.json()["skills"] == []

        enable_resp = await ac.post("/api/skills/api-disabled/enable")
        assert enable_resp.status_code == 200
        assert enable_resp.json()["disabled_count"] == 1

        after_enable = await ac.get("/api/skills")
        assert after_enable.status_code == 200
        assert [skill["id"] for skill in after_enable.json()["skills"]] == ["api-disabled"]

        validate_resp = await ac.post("/api/skills/validate")
        assert validate_resp.status_code == 200
        assert validate_resp.json()["invalid_count"] == 1

        reload_resp = await ac.post("/api/skills/reload")
        assert reload_resp.status_code == 200
        assert reload_resp.json()["total_files"] == 3
