from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def _default_store_path() -> Path:
    override = (os.getenv("WEAVER_DATA_DIR") or "").strip()
    if override:
        root = Path(override).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        return root / "channels.json"
    return Path("data/channels.json")


class ChannelStore:
    """Small persistent mapping from IM conversations to Weaver thread IDs."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _default_store_path()
        self._lock = threading.Lock()
        self._data = self._load()

    @staticmethod
    def _key(channel: str, chat_id: str, topic_id: str | None) -> str:
        return f"{channel}:{chat_id}:{topic_id or chat_id}"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"threads": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            return data if isinstance(data, dict) else {"threads": {}}
        except Exception:
            return {"threads": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get_thread_id(self, channel: str, chat_id: str, topic_id: str | None) -> str | None:
        with self._lock:
            value = self._data.get("threads", {}).get(self._key(channel, chat_id, topic_id))
            return value if isinstance(value, str) else None

    def set_thread_id(self, channel: str, chat_id: str, topic_id: str | None, thread_id: str) -> None:
        with self._lock:
            self._data.setdefault("threads", {})[self._key(channel, chat_id, topic_id)] = thread_id
            self._save()

    def clear_thread(self, channel: str, chat_id: str, topic_id: str | None) -> None:
        with self._lock:
            self._data.setdefault("threads", {}).pop(self._key(channel, chat_id, topic_id), None)
            self._save()
