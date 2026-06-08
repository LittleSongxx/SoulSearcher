from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _workspace_root() -> Path:
    override = os.environ.get("WEAVER_RESEARCH_WORKSPACE_PATH", "").strip()
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        return root
    return (Path.cwd() / "data" / "research_workspaces").resolve()


def _safe_thread_id(thread_id: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(thread_id or "default")).strip("._")
    return text or "default"


@dataclass(frozen=True)
class ResearchWorkspace:
    thread_id: str
    root: Path

    @classmethod
    def for_thread(cls, thread_id: str) -> "ResearchWorkspace":
        root = _workspace_root() / _safe_thread_id(thread_id)
        root.mkdir(parents=True, exist_ok=True)
        return cls(thread_id=thread_id, root=root)

    def artifact(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "path": str(self.root),
        }

    def write_json(self, relative_path: str, payload: Any) -> Path:
        path = self._path(relative_path)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def write_jsonl(self, relative_path: str, rows: list[dict[str, Any]]) -> Path:
        path = self._path(relative_path)
        lines = [
            json.dumps(row, ensure_ascii=False, default=str)
            for row in rows
            if isinstance(row, dict)
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self._path(relative_path)
        path.write_text(str(content or ""), encoding="utf-8")
        return path

    def _path(self, relative_path: str) -> Path:
        safe = Path(str(relative_path).lstrip("/"))
        if any(part == ".." for part in safe.parts):
            raise ValueError("Workspace paths cannot contain '..'")
        path = self.root / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def get_research_workspace(thread_id: str) -> ResearchWorkspace:
    return ResearchWorkspace.for_thread(thread_id)
