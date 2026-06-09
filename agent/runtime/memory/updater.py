"""LLM-driven memory updater — extracts facts and context from conversations."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fact_content_key(content: Any) -> str | None:
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped.casefold() if stripped else None


_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document)"
    r"|file\s+upload|/mnt/user-data/uploads/|<uploaded_files>)[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions(memory_data: dict[str, Any]) -> dict[str, Any]:
    """Remove upload-event sentences from memory."""
    profile = memory_data.get("profile", {})
    if isinstance(profile, dict):
        for key, value in list(profile.items()):
            if isinstance(value, str):
                cleaned = _UPLOAD_SENTENCE_RE.sub("", value).strip()
                profile[key] = re.sub(r"  +", " ", cleaned)
            elif isinstance(value, list):
                cleaned_values = []
                for item in value:
                    cleaned = _UPLOAD_SENTENCE_RE.sub("", str(item)).strip()
                    cleaned = re.sub(r"  +", " ", cleaned)
                    if cleaned:
                        cleaned_values.append(cleaned)
                profile[key] = cleaned_values
            elif isinstance(value, dict):
                cleaned_map = {}
                for pref_key, pref_value in value.items():
                    cleaned = _UPLOAD_SENTENCE_RE.sub("", str(pref_value)).strip()
                    cleaned = re.sub(r"  +", " ", cleaned)
                    if str(pref_key).strip() and cleaned:
                        cleaned_map[str(pref_key)] = cleaned
                profile[key] = cleaned_map

    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for val in section_data.values():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]
    return memory_data


class MemoryUpdater:
    """Uses LLM to extract memory from conversations."""

    def update_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
        user_id: str | None = None,
    ) -> bool:
        """Synchronously update memory from conversation messages."""
        if not messages:
            return False

        try:
            from agent.runtime.memory.storage import get_memory_storage, create_empty_memory
            from agent.runtime.memory.prompt import MEMORY_UPDATE_PROMPT, format_conversation_for_update

            storage = get_memory_storage()
            current = storage.load(agent_name, user_id=user_id)
            conversation = format_conversation_for_update(messages)
            if not conversation.strip():
                return False

            import llm_client
            prompt = MEMORY_UPDATE_PROMPT.format(
                current_memory=json.dumps(current, indent=2),
                conversation=conversation,
            )

            response = llm_client.chat(prompt)
            response_text = response if isinstance(response, str) else str(response)

            # Parse fenced JSON
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            update_data = json.loads(response_text)
            updated = self._apply_updates(copy.deepcopy(current), update_data, thread_id)
            updated = _strip_upload_mentions(updated)
            return storage.save(updated, agent_name, user_id=user_id)

        except json.JSONDecodeError:
            logger.warning("Failed to parse memory update LLM response")
            return False
        except Exception:
            logger.exception("Memory update failed")
            return False

    def _apply_updates(
        self,
        current: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None,
    ) -> dict[str, Any]:
        from agent.runtime.memory.storage import normalize_memory_data

        current = normalize_memory_data(current)
        max_facts = getattr(settings, "memory_max_facts", 100)
        confidence_threshold = getattr(settings, "memory_fact_confidence_threshold", 0.7)
        now = _utc_now_iso()

        # Update structured profile fields. Values may be direct scalars/lists/maps
        # or wrapped as {"shouldUpdate": true, "value": ...}.
        self._apply_profile_updates(current, update_data.get("profile", {}))

        # Update user summaries
        user_updates = update_data.get("user", {})
        for section in ("workContext", "personalContext", "topOfMind"):
            sdata = user_updates.get(section, {})
            if sdata.get("shouldUpdate") and sdata.get("summary"):
                current["user"][section] = {"summary": sdata["summary"], "updatedAt": now}

        # Update history summaries
        history_updates = update_data.get("history", {})
        for section in ("recentMonths", "earlierContext", "longTermBackground"):
            sdata = history_updates.get(section, {})
            if sdata.get("shouldUpdate") and sdata.get("summary"):
                current["history"][section] = {"summary": sdata["summary"], "updatedAt": now}

        # Remove facts
        to_remove = set(update_data.get("factsToRemove", []))
        if to_remove:
            current["facts"] = [f for f in current.get("facts", []) if f.get("id") not in to_remove]

        # Add new facts
        existing_keys = {
            key for key in (_fact_content_key(f.get("content")) for f in current.get("facts", []))
            if key is not None
        }
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence < confidence_threshold:
                continue
            content = fact.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            key = _fact_content_key(content.strip())
            if key and key in existing_keys:
                continue

            entry = {
                "id": f"fact_{uuid.uuid4().hex[:8]}",
                "content": content.strip(),
                "category": fact.get("category", "context"),
                "confidence": confidence,
                "createdAt": now,
                "source": thread_id or "unknown",
            }
            current["facts"].append(entry)
            if key:
                existing_keys.add(key)

        # Enforce max facts limit
        if len(current["facts"]) > max_facts:
            current["facts"] = sorted(
                current["facts"], key=lambda f: f.get("confidence", 0), reverse=True
            )[:max_facts]

        return current

    def _apply_profile_updates(
        self,
        current: dict[str, Any],
        profile_updates: Any,
    ) -> None:
        if not isinstance(profile_updates, dict):
            return

        profile = current.setdefault("profile", {})
        if not isinstance(profile, dict):
            profile = {}
            current["profile"] = profile

        aliases = {
            "role": ("role",),
            "expertise": ("expertise", "expertiseLevel", "expertise_level"),
            "language": ("language", "languagePreference", "language_preference"),
            "format": ("format", "formatPreference", "format_preference"),
            "verbosity": ("verbosity", "verbosityPreference", "verbosity_preference"),
            "preferredSources": ("preferredSources", "preferred_sources"),
            "researchGoals": ("researchGoals", "research_goals", "goals"),
            "preferences": ("preferences",),
        }

        for field_name, candidates in aliases.items():
            raw_value = None
            for candidate in candidates:
                if candidate in profile_updates:
                    raw_value = profile_updates.get(candidate)
                    break
            value = self._unwrap_profile_update(raw_value)
            if value is None:
                continue

            if field_name == "preferences":
                if isinstance(value, dict):
                    existing = profile.get("preferences", {})
                    if not isinstance(existing, dict):
                        existing = {}
                    for key, pref_value in value.items():
                        clean_key = str(key).strip()
                        clean_value = str(pref_value).strip()
                        if clean_key and clean_value:
                            existing[clean_key] = clean_value
                    profile["preferences"] = existing
                continue

            if field_name in {"preferredSources", "researchGoals"}:
                values = self._coerce_string_list(value)
                if values:
                    merged = []
                    seen = set()
                    for item in list(profile.get(field_name, [])) + values:
                        normalized = str(item).strip()
                        key = normalized.casefold()
                        if normalized and key not in seen:
                            merged.append(normalized)
                            seen.add(key)
                    profile[field_name] = merged[:20]
                continue

            if isinstance(value, (str, int, float, bool)):
                text = str(value).strip()
                if text:
                    profile[field_name] = text

    @staticmethod
    def _unwrap_profile_update(raw_value: Any) -> Any:
        if raw_value is None:
            return None
        if isinstance(raw_value, dict) and "shouldUpdate" in raw_value:
            if not raw_value.get("shouldUpdate"):
                return None
            for key in ("value", "summary", "items", "content", "preferences"):
                if key in raw_value:
                    return raw_value.get(key)
            return None
        return raw_value

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            parts = re.split(r"[,;\n]", value)
            return [part.strip() for part in parts if part.strip()]
        return []


def get_memory_data(agent_name: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    from agent.runtime.memory.storage import get_memory_storage
    return get_memory_storage().load(agent_name, user_id=user_id)


def update_memory_from_conversation(
    messages: list[Any],
    thread_id: str | None = None,
    agent_name: str | None = None,
    correction_detected: bool = False,
    reinforcement_detected: bool = False,
    user_id: str | None = None,
) -> bool:
    updater = MemoryUpdater()
    return updater.update_memory(
        messages, thread_id, agent_name, correction_detected, reinforcement_detected, user_id
    )


def create_memory_fact(
    content: str,
    category: str = "context",
    confidence: float = 0.5,
    agent_name: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    from agent.runtime.memory.storage import get_memory_storage, create_empty_memory

    content = content.strip()
    if not content:
        raise ValueError("content is required")

    storage = get_memory_storage()
    memory = storage.load(agent_name, user_id=user_id)
    facts = list(memory.get("facts", []))

    now = _utc_now_iso()
    facts.append({
        "id": f"fact_{uuid.uuid4().hex[:8]}",
        "content": content,
        "category": category.strip() or "context",
        "confidence": max(0.0, min(1.0, confidence)),
        "createdAt": now,
        "source": "manual",
    })
    memory["facts"] = facts
    storage.save(memory, agent_name, user_id=user_id)
    return memory


def delete_memory_fact(
    fact_id: str,
    agent_name: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    from agent.runtime.memory.storage import get_memory_storage
    storage = get_memory_storage()
    memory = storage.load(agent_name, user_id=user_id)
    facts = memory.get("facts", [])
    updated = [f for f in facts if f.get("id") != fact_id]
    if len(updated) == len(facts):
        raise KeyError(fact_id)
    memory["facts"] = updated
    storage.save(memory, agent_name, user_id=user_id)
    return memory
