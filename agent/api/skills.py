from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel


class SkillListResponse(BaseModel):
    skills: list[dict[str, Any]]


class SkillDetailResponse(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool
    allowed_tools: list[str] | None = None
    license: str | None = None
    path: str
    container_path: str


class SkillInstallResponse(BaseModel):
    success: bool
    skill_name: str
    message: str


class SkillHistoryResponse(BaseModel):
    name: str
    history: list[dict[str, Any]]


def _clear_skills_cache() -> None:
    from agent.skills.prompt import clear_skills_prompt_cache

    clear_skills_prompt_cache()


def build_skills_router() -> APIRouter:
    router = APIRouter(tags=["skills"])

    @router.get("/api/skills", response_model=SkillListResponse)
    async def list_skills(enabled_only: bool = False):
        """List all available skills (public and custom)."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            skills = storage.load_skills(enabled_only=enabled_only)
            return {
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "category": s.category.value,
                        "enabled": s.enabled,
                        "allowed_tools": s.allowed_tools,
                        "license": s.license,
                        "path": s.skill_path,
                    }
                    for s in skills
                ]
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/skills/{name}", response_model=SkillDetailResponse)
    async def get_skill(name: str):
        """Get details for a specific skill, including container path info."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            skills = storage.load_skills()
            for s in skills:
                if s.name == name:
                    return {
                        "name": s.name,
                        "description": s.description,
                        "category": s.category.value,
                        "enabled": s.enabled,
                        "allowed_tools": s.allowed_tools,
                        "license": s.license,
                        "path": s.skill_path,
                        "container_path": s.get_container_file_path(
                            storage.get_container_root()
                        ),
                    }
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.put("/api/skills/{name}")
    async def update_skill(name: str, enabled: bool = True):
        """Enable or disable a skill by updating extensions_config.json."""
        try:
            from common.extensions_config import (
                ExtensionsConfig,
                reload_extensions_config,
            )

            config = ExtensionsConfig.from_file()
            config.skills[name] = {"enabled": enabled}
            config_path = ExtensionsConfig.resolve_config_path()
            if config_path:
                config_path.write_text(
                    json.dumps(
                        config.model_dump(by_alias=True),
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            reload_extensions_config()
            _clear_skills_cache()
            return {"name": name, "enabled": enabled}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/skills/install", response_model=SkillInstallResponse)
    async def install_skill(file: UploadFile = None):
        """Install a skill from a .skill ZIP archive."""
        if not file or not file.filename or not file.filename.endswith(".skill"):
            raise HTTPException(status_code=400, detail="A .skill file is required")
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            with tempfile.NamedTemporaryFile(suffix=".skill", delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = Path(tmp.name)
            try:
                result = await storage.ainstall_skill_from_archive(str(tmp_path))
            finally:
                tmp_path.unlink(missing_ok=True)
            _clear_skills_cache()
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/skills/custom")
    async def list_custom_skills():
        """List only custom (user-authored) skills."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            skills = storage.load_skills(enabled_only=False)
            custom = [s for s in skills if s.category.value == "custom"]
            return {
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "enabled": s.enabled,
                        "allowed_tools": s.allowed_tools,
                        "license": s.license,
                        "path": s.skill_path,
                    }
                    for s in custom
                ]
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/api/skills/custom/{name}")
    async def get_custom_skill(name: str):
        """Get the full SKILL.md content for a custom skill."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            if not storage.custom_skill_exists(name):
                raise HTTPException(
                    status_code=404, detail=f"Custom skill '{name}' not found"
                )
            content = storage.read_custom_skill(name)
            return {"name": name, "content": content}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.put("/api/skills/custom/{name}")
    async def edit_custom_skill(name: str, content: str):
        """Edit a custom skill's SKILL.md content."""
        try:
            from agent.skills.security_scanner import scan_skill_content
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            storage.ensure_custom_skill_is_editable(name)
            storage.validate_skill_markdown_content(name, content)
            result = await scan_skill_content(
                content, executable=False, location=f"{name}/SKILL.md"
            )
            if result.decision == "block":
                raise HTTPException(
                    status_code=400,
                    detail=f"Security scan blocked: {result.reason}",
                )
            storage.write_custom_skill(name, "SKILL.md", content)
            _clear_skills_cache()
            return {"name": name, "message": f"Custom skill '{name}' updated"}
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.delete("/api/skills/custom/{name}")
    async def delete_custom_skill(name: str):
        """Delete a custom skill."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            storage.delete_custom_skill(name)
            _clear_skills_cache()
            return {"name": name, "message": f"Custom skill '{name}' deleted"}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get(
        "/api/skills/custom/{name}/history",
        response_model=SkillHistoryResponse,
    )
    async def get_skill_history(name: str):
        """Get the edit history for a custom skill."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            history = storage.read_history(name)
            return {"name": name, "history": history}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/skills/custom/{name}/rollback")
    async def rollback_skill(name: str, version: int = 0):
        """Rollback a custom skill to a previous version in its history."""
        try:
            from agent.skills.storage import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            history = storage.read_history(name)
            if not history:
                raise HTTPException(
                    status_code=404, detail=f"No history found for skill '{name}'"
                )
            if version < 0 or version >= len(history):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid version index {version}. "
                        f"Valid range: 0-{len(history) - 1}"
                    ),
                )
            entry = history[version]
            prev = entry.get("prev_content")
            if not prev:
                raise HTTPException(
                    status_code=400,
                    detail="Selected version has no previous content to restore",
                )
            storage.write_custom_skill(name, "SKILL.md", prev)
            _clear_skills_cache()
            return {
                "name": name,
                "message": f"Rolled back to version {version}",
                "version": version,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
