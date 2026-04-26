import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import skills_loader


def _write_skill(path: Path, frontmatter: str, body: str = '# Role\n\nYou are a skill.') -> None:
    path.write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n\n{textwrap.dedent(body).strip()}\n",
        encoding="utf-8",
    )


def test_validate_skills_parses_contracts_and_reports_invalid_manifest(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill(
        skills_dir / "valid.md",
        """
        id: valid-skill
        name: Valid Skill
        description: A valid skill manifest
        category: tool
        mode: agent
        tools:
          - python
        permissions:
          network: false
          filesystem: none
          shell: false
          browser: false
          sandbox: false
          external_apis: false
        input_contract:
          - name: prompt
            description: The task prompt
            type: string
            required: true
        output_contract:
          - name: answer
            description: Final result
            type: markdown
            required: true
        """,
    )
    _write_skill(
        skills_dir / "invalid.md",
        """
        id: invalid-skill
        name: Invalid Skill
        category: tool
        mode: agent
        tools:
          - definitely_unknown_tool
        """,
    )

    snapshot = skills_loader.validate_skills(skills_dir)

    assert snapshot.total_files == 2
    assert snapshot.valid_count == 1
    assert snapshot.invalid_count == 1
    assert any("unknown tool 'definitely_unknown_tool'" in issue.message for issue in snapshot.issues)

    valid_skill = next(skill for skill in snapshot.skills if skill.id == "valid-skill")
    assert valid_skill.is_valid is True
    assert valid_skill.input_contract[0].name == "prompt"
    assert valid_skill.output_contract[0].name == "answer"
    assert valid_skill.to_enabled_tools()["python"] is True
    assert valid_skill.to_enabled_tools()["web_search"] is False



def test_validate_skills_reports_duplicate_ids(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    frontmatter = """
    id: duplicate-skill
    name: Duplicate Skill
    description: Duplicate manifest id
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
    """

    _write_skill(skills_dir / "a.md", frontmatter)
    _write_skill(skills_dir / "b.md", frontmatter)

    snapshot = skills_loader.validate_skills(skills_dir)

    assert snapshot.valid_count == 0
    assert snapshot.invalid_count == 2
    assert any("duplicate skill id 'duplicate-skill'" in issue.message for issue in snapshot.issues)



def test_update_skill_status_persists_override(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    state_dir = tmp_path / "state"

    _write_skill(
        skills_dir / "toggle.md",
        """
        id: toggle-skill
        name: Toggle Skill
        description: A skill that can be toggled
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
    disabled_snapshot = skills_loader.update_skill_status("toggle-skill", "disabled")
    disabled_skill = next(skill for skill in disabled_snapshot.skills if skill.id == "toggle-skill")
    assert disabled_skill.status == "disabled"
    assert skills_loader.load_all_skills(use_cache=False) == []

    enabled_snapshot = skills_loader.update_skill_status("toggle-skill", "enabled")
    enabled_skill = next(skill for skill in enabled_snapshot.skills if skill.id == "toggle-skill")
    assert enabled_skill.status == "enabled"
    assert [skill.id for skill in skills_loader.load_all_skills(use_cache=False)] == ["toggle-skill"]
