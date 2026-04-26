"""
Skills Loader — parse Markdown skill files from the skills/ directory.

Each skill is a .md file with YAML frontmatter (metadata) and a Markdown body
(used as the system_prompt for the LLM).

Example file (skills/code-assistant.md):

    ---
    id: code-assistant
    name: 代码助手
    name_en: Code Assistant
    ...
    ---
    You are a professional code assistant...
"""

from __future__ import annotations

import logging
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_REGISTRY_CACHE: Optional["SkillRegistrySnapshot"] = None

ALLOWED_SKILL_MODES = {"direct", "agent", "deep"}
ALLOWED_SKILL_CATEGORIES = {"research", "code", "writing", "data", "creative", "tool"}
ALLOWED_SKILL_STATUSES = {"enabled", "disabled", "deprecated", "draft"}
ALLOWED_TOOL_POLICIES = {"strict", "default"}
KNOWN_TOOL_KEYS = {
    "ask_human",
    "bash",
    "browser",
    "browser_use",
    "computer_use",
    "crawl",
    "mcp",
    "planning",
    "presentation_outline",
    "presentation_v2",
    "python",
    "rag",
    "sandbox_browser",
    "sandbox_daytona",
    "sandbox_files",
    "sandbox_image_edit",
    "sandbox_presentation",
    "sandbox_sheets",
    "sandbox_shell",
    "sandbox_vision",
    "sandbox_web_dev",
    "sandbox_web_search",
    "str_replace",
    "task_list",
    "web_search",
}
_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SkillValidationIssue:
    severity: str
    message: str
    field: str = ""
    skill_id: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
            "skill_id": self.skill_id,
            "source": self.source,
        }


@dataclass
class SkillIOField:
    name: str
    description: str = ""
    type: str = "string"
    required: bool = False
    examples: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.type,
            "required": self.required,
            "examples": self.examples,
        }


@dataclass
class SkillDependency:
    name: str
    type: str = "runtime"
    required: bool = True
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }


@dataclass
class SkillPermissions:
    network: bool = False
    filesystem: str = "none"
    shell: bool = False
    browser: bool = False
    sandbox: bool = False
    external_apis: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "network": self.network,
            "filesystem": self.filesystem,
            "shell": self.shell,
            "browser": self.browser,
            "sandbox": self.sandbox,
            "external_apis": self.external_apis,
        }


@dataclass
class SkillRegistrySnapshot:
    skills: List["SkillProfile"] = field(default_factory=list)
    issues: List[SkillValidationIssue] = field(default_factory=list)
    loaded_at: str = field(default_factory=_utc_now_iso)
    skills_dir: str = ""
    total_files: int = 0
    invalid_count: int = 0

    @property
    def valid_count(self) -> int:
        return len([skill for skill in self.skills if skill.is_valid])

    @property
    def disabled_count(self) -> int:
        return len([skill for skill in self.skills if skill.is_valid and skill.status != "enabled"])

    def to_dict(self, *, include_skills: bool = False) -> Dict[str, Any]:
        payload = {
            "loaded_at": self.loaded_at,
            "skills_dir": self.skills_dir,
            "total_files": self.total_files,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "disabled_count": self.disabled_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if include_skills:
            payload["skills"] = [
                skill.to_summary_dict(include_diagnostics=True) for skill in self.skills
            ]
        return payload


@dataclass(frozen=True)
class SkillsStatePaths:
    root: Path
    file: Path


@dataclass
class SkillProfile:
    """Parsed representation of a single skill .md file."""

    id: str
    name: str
    name_en: str = ""
    description: str = ""
    description_en: str = ""
    icon: str = "⚡"
    category: str = "tool"  # research | code | writing | data | creative | tool
    mode: str = "agent"  # direct | agent | deep
    tools: List[str] = field(default_factory=list)
    example_queries: List[str] = field(default_factory=list)
    is_preset: bool = True
    version: str = "1.0.0"
    status: str = "enabled"
    tool_policy: str = "strict"
    tags: List[str] = field(default_factory=list)
    runtime: Dict[str, Any] = field(default_factory=dict)
    input_contract: List[SkillIOField] = field(default_factory=list)
    output_contract: List[SkillIOField] = field(default_factory=list)
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    dependencies: List[SkillDependency] = field(default_factory=list)
    validation_issues: List[SkillValidationIssue] = field(default_factory=list)
    system_prompt: str = ""
    updated_at: str = ""
    loaded_at: str = field(default_factory=_utc_now_iso)
    # Source file path (for debugging)
    _source: str = ""
    _raw_meta: Dict[str, Any] = field(default_factory=dict)

    # ---- helpers used by the backend ----

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.validation_issues)

    @property
    def is_enabled(self) -> bool:
        return self.status == "enabled"

    def to_enabled_tools(self) -> Dict[str, bool]:
        """Convert the tools list to an ``enabled_tools`` dict compatible with AgentProfile."""
        enabled: Dict[str, bool] = {}
        if self.tool_policy == "strict":
            enabled = {tool: False for tool in KNOWN_TOOL_KEYS}
        for tool in self.tools:
            enabled[tool] = True
        return enabled

    def to_agent_profile_dict(self) -> Dict[str, Any]:
        """Return a dict that can be used as ``configurable.agent_profile``."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "model": "",
            "enabled_tools": self.to_enabled_tools(),
            "mcp_servers": None,
            "metadata": {
                "skill": True,
                "category": self.category,
                "version": self.version,
                "status": self.status,
                "tool_policy": self.tool_policy,
                "tags": self.tags,
                "runtime": self.runtime,
                "permissions": self.permissions.to_dict(),
                "dependencies": [item.to_dict() for item in self.dependencies],
                "input_contract": [item.to_dict() for item in self.input_contract],
                "output_contract": [item.to_dict() for item in self.output_contract],
            },
        }

    def to_summary_dict(self, *, include_diagnostics: bool = False) -> Dict[str, Any]:
        """Metadata-only dict for the listing API (no full prompt)."""
        payload = {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "description": self.description,
            "description_en": self.description_en,
            "icon": self.icon,
            "category": self.category,
            "mode": self.mode,
            "tools": self.tools,
            "example_queries": self.example_queries,
            "is_preset": self.is_preset,
            "version": self.version,
            "status": self.status,
            "tool_policy": self.tool_policy,
            "tags": self.tags,
            "runtime": self.runtime,
            "permissions": self.permissions.to_dict(),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "input_contract": [item.to_dict() for item in self.input_contract],
            "output_contract": [item.to_dict() for item in self.output_contract],
            "updated_at": self.updated_at,
            "loaded_at": self.loaded_at,
            "source": self._source,
            "validation": {
                "valid": self.is_valid,
                "issues": [issue.to_dict() for issue in self.validation_issues],
            },
        }
        if not include_diagnostics:
            payload.pop("source", None)
        return payload

    def to_full_dict(self) -> Dict[str, Any]:
        """Full dict including system_prompt."""
        d = self.to_summary_dict(include_diagnostics=True)
        d["system_prompt"] = self.system_prompt
        return d


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _normalize_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    text = str(value).strip()
    return [text] if text else []


def _infer_permissions(tools: List[str]) -> SkillPermissions:
    browser_tools = {"browser", "browser_use", "sandbox_browser", "sandbox_web_search", "computer_use"}
    network_tools = browser_tools | {"web_search", "crawl", "mcp"}
    sandbox_tools = {
        "sandbox_browser",
        "sandbox_daytona",
        "sandbox_files",
        "sandbox_image_edit",
        "sandbox_presentation",
        "sandbox_sheets",
        "sandbox_shell",
        "sandbox_vision",
        "sandbox_web_dev",
        "sandbox_web_search",
        "presentation_outline",
        "presentation_v2",
    }
    filesystem_tools = {
        "sandbox_files",
        "sandbox_sheets",
        "sandbox_presentation",
        "presentation_outline",
        "presentation_v2",
    }
    return SkillPermissions(
        network=any(tool in network_tools for tool in tools),
        filesystem="sandbox" if any(tool in filesystem_tools for tool in tools) else "none",
        shell=any(tool in {"bash", "sandbox_shell"} for tool in tools),
        browser=any(tool in browser_tools for tool in tools),
        sandbox=any(tool in sandbox_tools for tool in tools),
        external_apis=any(tool in {"web_search", "crawl", "mcp"} for tool in tools),
    )


def _parse_contract_fields(
    value: Any,
    *,
    field_name: str,
    issues: List[SkillValidationIssue],
    skill_id: str,
    source: str,
) -> List[SkillIOField]:
    if value in (None, ""):
        return []
    items: List[Any]
    if isinstance(value, dict):
        items = [{"name": key, **(item if isinstance(item, dict) else {"description": item})} for key, item in value.items()]
    elif isinstance(value, list):
        items = value
    else:
        issues.append(
            SkillValidationIssue(
                severity="error",
                message=f"{field_name} must be a list or mapping",
                field=field_name,
                skill_id=skill_id,
                source=source,
            )
        )
        return []

    fields: List[SkillIOField] = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {"name": item, "description": ""}
        if not isinstance(item, dict):
            issues.append(
                SkillValidationIssue(
                    severity="error",
                    message=f"{field_name}[{index}] must be an object",
                    field=field_name,
                    skill_id=skill_id,
                    source=source,
                )
            )
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name:
            issues.append(
                SkillValidationIssue(
                    severity="error",
                    message=f"{field_name}[{index}] is missing name",
                    field=field_name,
                    skill_id=skill_id,
                    source=source,
                )
            )
        if not description:
            issues.append(
                SkillValidationIssue(
                    severity="error",
                    message=f"{field_name}[{index}] is missing description",
                    field=field_name,
                    skill_id=skill_id,
                    source=source,
                )
            )
        fields.append(
            SkillIOField(
                name=name,
                description=description,
                type=str(item.get("type") or "string"),
                required=bool(item.get("required", False)),
                examples=_normalize_string_list(item.get("examples")),
            )
        )
    return fields


def _parse_dependencies(value: Any) -> List[SkillDependency]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, list) else [value]
    dependencies: List[SkillDependency] = []
    for item in items:
        if isinstance(item, str):
            dependencies.append(SkillDependency(name=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        dependencies.append(
            SkillDependency(
                name=name,
                type=str(item.get("type") or "runtime"),
                required=bool(item.get("required", True)),
                description=str(item.get("description") or "").strip(),
            )
        )
    return dependencies


def _parse_permissions(value: Any, tools: List[str]) -> SkillPermissions:
    inferred = _infer_permissions(tools)
    if not isinstance(value, dict):
        return inferred
    return SkillPermissions(
        network=bool(value.get("network", inferred.network)),
        filesystem=str(value.get("filesystem") or inferred.filesystem),
        shell=bool(value.get("shell", inferred.shell)),
        browser=bool(value.get("browser", inferred.browser)),
        sandbox=bool(value.get("sandbox", inferred.sandbox)),
        external_apis=bool(value.get("external_apis", inferred.external_apis)),
    )


def _validate_skill(skill: SkillProfile) -> List[SkillValidationIssue]:
    issues = list(skill.validation_issues)

    if not skill.id:
        issues.append(SkillValidationIssue(severity="error", message="id is required", field="id", skill_id=skill.id, source=skill._source))
    elif not _SKILL_ID_RE.match(skill.id):
        issues.append(SkillValidationIssue(severity="error", message="id must match ^[a-z0-9][a-z0-9_.-]*$", field="id", skill_id=skill.id, source=skill._source))

    for field_name in ("name", "description"):
        if not str(getattr(skill, field_name) or "").strip():
            issues.append(
                SkillValidationIssue(
                    severity="error",
                    message=f"{field_name} is required",
                    field=field_name,
                    skill_id=skill.id,
                    source=skill._source,
                )
            )

    if skill.category not in ALLOWED_SKILL_CATEGORIES:
        issues.append(SkillValidationIssue(severity="error", message=f"category must be one of {sorted(ALLOWED_SKILL_CATEGORIES)}", field="category", skill_id=skill.id, source=skill._source))
    if skill.mode not in ALLOWED_SKILL_MODES:
        issues.append(SkillValidationIssue(severity="error", message=f"mode must be one of {sorted(ALLOWED_SKILL_MODES)}", field="mode", skill_id=skill.id, source=skill._source))
    if skill.status not in ALLOWED_SKILL_STATUSES:
        issues.append(SkillValidationIssue(severity="error", message=f"status must be one of {sorted(ALLOWED_SKILL_STATUSES)}", field="status", skill_id=skill.id, source=skill._source))
    if skill.tool_policy not in ALLOWED_TOOL_POLICIES:
        issues.append(SkillValidationIssue(severity="error", message=f"tool_policy must be one of {sorted(ALLOWED_TOOL_POLICIES)}", field="tool_policy", skill_id=skill.id, source=skill._source))
    if not skill.system_prompt.strip():
        issues.append(SkillValidationIssue(severity="error", message="system_prompt body is required", field="system_prompt", skill_id=skill.id, source=skill._source))

    unknown_tools = [tool for tool in skill.tools if tool not in KNOWN_TOOL_KEYS]
    for tool in unknown_tools:
        issues.append(SkillValidationIssue(severity="error", message=f"unknown tool '{tool}'", field="tools", skill_id=skill.id, source=skill._source))

    if skill.permissions.filesystem not in {"none", "read", "write", "sandbox"}:
        issues.append(SkillValidationIssue(severity="error", message="permissions.filesystem must be one of ['none', 'read', 'write', 'sandbox']", field="permissions.filesystem", skill_id=skill.id, source=skill._source))

    if any(tool in {"bash", "sandbox_shell"} for tool in skill.tools) and not skill.permissions.shell:
        issues.append(SkillValidationIssue(severity="error", message="permissions.shell must be true when shell tools are enabled", field="permissions.shell", skill_id=skill.id, source=skill._source))
    if any(tool in {"browser", "browser_use", "sandbox_browser", "sandbox_web_search", "computer_use"} for tool in skill.tools) and not skill.permissions.browser:
        issues.append(SkillValidationIssue(severity="error", message="permissions.browser must be true when browser tools are enabled", field="permissions.browser", skill_id=skill.id, source=skill._source))
    if any(tool in {"web_search", "crawl", "browser", "browser_use", "sandbox_browser", "sandbox_web_search", "mcp"} for tool in skill.tools) and not skill.permissions.network:
        issues.append(SkillValidationIssue(severity="error", message="permissions.network must be true when network tools are enabled", field="permissions.network", skill_id=skill.id, source=skill._source))
    if any(tool.startswith("sandbox_") or tool in {"presentation_outline", "presentation_v2"} for tool in skill.tools) and not skill.permissions.sandbox:
        issues.append(SkillValidationIssue(severity="error", message="permissions.sandbox must be true when sandbox-backed tools are enabled", field="permissions.sandbox", skill_id=skill.id, source=skill._source))
    if any(tool in {"sandbox_files", "sandbox_sheets", "sandbox_presentation", "presentation_outline", "presentation_v2"} for tool in skill.tools) and skill.permissions.filesystem == "none":
        issues.append(SkillValidationIssue(severity="error", message="permissions.filesystem must allow file access when file-producing tools are enabled", field="permissions.filesystem", skill_id=skill.id, source=skill._source))

    return issues


def _state_paths(project_root: Optional[Path] = None) -> SkillsStatePaths:
    override = (os.getenv("WEAVER_DATA_DIR") or "").strip()
    if override:
        data_dir = Path(override).expanduser()
        if not data_dir.is_absolute():
            data_dir = (Path.cwd() / data_dir).resolve()
        return SkillsStatePaths(root=data_dir, file=data_dir / "skills_state.json")
    root = project_root or Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    return SkillsStatePaths(root=data_dir, file=data_dir / "skills_state.json")


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_skill_state(paths: Optional[SkillsStatePaths] = None) -> Dict[str, Any]:
    paths = paths or _state_paths()
    with _STATE_LOCK:
        if not paths.file.exists():
            return {"status_overrides": {}}
        try:
            raw = json.loads(paths.file.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {"status_overrides": {}}
        if not isinstance(raw, dict):
            return {"status_overrides": {}}
        overrides = raw.get("status_overrides")
        if not isinstance(overrides, dict):
            raw["status_overrides"] = {}
        return raw


def _save_skill_state(payload: Dict[str, Any], paths: Optional[SkillsStatePaths] = None) -> None:
    paths = paths or _state_paths()
    payload = dict(payload)
    payload["updated_at"] = _utc_now_iso()
    with _STATE_LOCK:
        _atomic_write_json(paths.file, payload)


def _apply_status_override(skill_id: str, status: str, state: Dict[str, Any]) -> str:
    overrides = state.get("status_overrides") or {}
    if isinstance(overrides, dict):
        override = str(overrides.get(skill_id) or "").strip().lower()
        if override in ALLOWED_SKILL_STATUSES:
            return override
    return status


def _parse_skill_file(path: Path, *, state: Optional[Dict[str, Any]] = None) -> Optional[SkillProfile]:
    """Parse a single .md skill file into a SkillProfile."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read skill file %s: %s", path, exc)
        return None

    # Split YAML frontmatter from body
    if not raw.startswith("---"):
        logger.warning("Skill file %s missing YAML frontmatter (no leading ---)", path)
        return None

    parts = raw.split("---", 2)
    if len(parts) < 3:
        logger.warning("Skill file %s has malformed frontmatter", path)
        return None

    frontmatter_str = parts[1].strip()
    body = parts[2].strip()

    try:
        meta = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as exc:
        logger.warning("Skill file %s has invalid YAML: %s", path, exc)
        return None

    if not isinstance(meta, dict):
        logger.warning("Skill file %s frontmatter is not a mapping", path)
        return None

    skill_id = meta.get("id") or path.stem
    tools = _normalize_string_list(meta.get("tools"))
    issues: List[SkillValidationIssue] = []
    skill = SkillProfile(
        id=str(skill_id).strip(),
        name=str(meta.get("name") or skill_id).strip(),
        name_en=str(meta.get("name_en") or "").strip(),
        description=str(meta.get("description") or "").strip(),
        description_en=str(meta.get("description_en") or "").strip(),
        icon=str(meta.get("icon") or "⚡").strip() or "⚡",
        category=str(meta.get("category") or "tool").strip().lower(),
        mode=str(meta.get("mode") or "agent").strip().lower(),
        tools=tools,
        example_queries=_normalize_string_list(meta.get("example_queries")),
        is_preset=bool(meta.get("is_preset", True)),
        version=str(meta.get("version") or "1.0.0").strip() or "1.0.0",
        status=str(meta.get("status") or "enabled").strip().lower() or "enabled",
        tool_policy=str(meta.get("tool_policy") or "strict").strip().lower() or "strict",
        tags=_normalize_string_list(meta.get("tags")),
        runtime=meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {},
        input_contract=_parse_contract_fields(
            meta.get("input_contract"),
            field_name="input_contract",
            issues=issues,
            skill_id=str(skill_id).strip(),
            source=str(path),
        ),
        output_contract=_parse_contract_fields(
            meta.get("output_contract"),
            field_name="output_contract",
            issues=issues,
            skill_id=str(skill_id).strip(),
            source=str(path),
        ),
        permissions=_parse_permissions(meta.get("permissions"), tools),
        dependencies=_parse_dependencies(meta.get("dependencies")),
        validation_issues=[],
        system_prompt=body,
        updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        _source=str(path),
        _raw_meta=meta,
    )
    skill.status = _apply_status_override(skill.id, skill.status, state or {})
    skill.validation_issues = _validate_skill(skill)
    for issue in issues:
        skill.validation_issues.append(issue)
    return skill


def _default_skills_dir() -> Path:
    """Return the default skills/ directory at the project root."""
    return Path(__file__).resolve().parents[1] / "skills"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _build_registry(skills_dir: Optional[Path] = None) -> SkillRegistrySnapshot:
    directory = skills_dir or _default_skills_dir()
    snapshot = SkillRegistrySnapshot(skills_dir=str(directory), total_files=0)
    if not directory.is_dir():
        logger.info("Skills directory %s does not exist; returning empty list", directory)
        return snapshot

    state = _load_skill_state()
    parsed_skills: List[SkillProfile] = []
    issues: List[SkillValidationIssue] = []
    files = sorted(directory.glob("*.md"))
    snapshot.total_files = len(files)

    for md_file in files:
        skill = _parse_skill_file(md_file, state=state)
        if skill is None:
            issues.append(
                SkillValidationIssue(
                    severity="error",
                    message="failed to parse skill file",
                    field="frontmatter",
                    skill_id=md_file.stem,
                    source=str(md_file),
                )
            )
            continue
        parsed_skills.append(skill)
        issues.extend(skill.validation_issues)

    counts: Dict[str, int] = {}
    for skill in parsed_skills:
        counts[skill.id] = counts.get(skill.id, 0) + 1

    for skill in parsed_skills:
        if counts.get(skill.id, 0) > 1:
            issue = SkillValidationIssue(
                severity="error",
                message=f"duplicate skill id '{skill.id}'",
                field="id",
                skill_id=skill.id,
                source=skill._source,
            )
            skill.validation_issues.append(issue)
            issues.append(issue)

    snapshot.skills = parsed_skills
    snapshot.issues = issues
    snapshot.invalid_count = len([skill for skill in parsed_skills if not skill.is_valid])
    return snapshot


def get_skill_registry(
    skills_dir: Optional[Path] = None,
    *,
    use_cache: bool = True,
) -> SkillRegistrySnapshot:
    global _REGISTRY_CACHE

    directory = skills_dir or _default_skills_dir()
    if use_cache and _REGISTRY_CACHE is not None and _REGISTRY_CACHE.skills_dir == str(directory):
        return _REGISTRY_CACHE

    snapshot = _build_registry(directory)
    with _LOCK:
        _REGISTRY_CACHE = snapshot
    logger.info(
        "Loaded %d skills (%d valid, %d invalid) from %s",
        len(snapshot.skills),
        snapshot.valid_count,
        snapshot.invalid_count,
        directory,
    )
    return snapshot


def validate_skills(skills_dir: Optional[Path] = None) -> SkillRegistrySnapshot:
    return _build_registry(skills_dir)


def load_all_skills(
    skills_dir: Optional[Path] = None,
    *,
    use_cache: bool = True,
    include_disabled: bool = False,
    include_invalid: bool = False,
) -> List[SkillProfile]:
    """
    Load and return all skill profiles from the skills/ directory.

    Results are cached in-memory after the first call.  Pass
    ``use_cache=False`` or call ``reload_skills()`` to refresh.
    """
    snapshot = get_skill_registry(skills_dir, use_cache=use_cache)
    skills = list(snapshot.skills)
    if not include_invalid:
        skills = [skill for skill in skills if skill.is_valid]
    if not include_disabled:
        skills = [skill for skill in skills if skill.status == "enabled"]
    return skills


def get_skill(
    skill_id: str,
    *,
    include_disabled: bool = True,
    include_invalid: bool = False,
    use_cache: bool = True,
) -> Optional[SkillProfile]:
    """Return a single skill by ID, or None."""
    for skill in load_all_skills(
        use_cache=use_cache,
        include_disabled=include_disabled,
        include_invalid=include_invalid,
    ):
        if skill.id == skill_id:
            return skill
    return None


def reload_skills(
    skills_dir: Optional[Path] = None,
    *,
    include_disabled: bool = False,
    include_invalid: bool = False,
) -> List[SkillProfile]:
    """Force-reload skills from disk (clears cache)."""
    global _REGISTRY_CACHE
    with _LOCK:
        _REGISTRY_CACHE = None
    return load_all_skills(
        skills_dir,
        use_cache=False,
        include_disabled=include_disabled,
        include_invalid=include_invalid,
    )


def reload_skill_registry(skills_dir: Optional[Path] = None) -> SkillRegistrySnapshot:
    global _REGISTRY_CACHE
    with _LOCK:
        _REGISTRY_CACHE = None
    return get_skill_registry(skills_dir, use_cache=False)


def update_skill_status(skill_id: str, status: str) -> SkillRegistrySnapshot:
    normalized_status = (status or "").strip().lower()
    if normalized_status not in {"enabled", "disabled"}:
        raise ValueError("status must be 'enabled' or 'disabled'")

    if not get_skill(skill_id, include_disabled=True, include_invalid=True):
        raise KeyError(skill_id)

    state = _load_skill_state()
    overrides = state.get("status_overrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    overrides[skill_id] = normalized_status
    state["status_overrides"] = overrides
    _save_skill_state(state)
    return reload_skill_registry()


def get_skill_profile(skill_id: str) -> Optional[SkillProfile]:
    return get_skill(skill_id, include_disabled=True, include_invalid=False)
