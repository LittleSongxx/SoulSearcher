from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

TODO_STATUSES = {"pending", "running", "completed", "blocked", "cancelled"}
TODO_COVERAGE_STATUSES = {"planned", "in_progress", "covered", "blocked", "cancelled"}
MAX_TODOS = 8
MAX_TITLE_CHARS = 160


def derive_todos_from_plan(plan: str, research_brief: str = "") -> list[dict[str, Any]]:
    """Derive initial research todos from an approved Markdown research plan."""
    candidates = _extract_subtopic_items(plan)
    if not candidates:
        candidates = _extract_list_items(plan)[:6]
    if not candidates:
        candidates = ["Complete the approved research brief"]

    todos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title in candidates:
        normalized, priority, dependencies = _normalize_title(title, priority_hint=len(todos) + 1)
        if not normalized:
            continue
        key = _dedupe_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        todos.append(
            _new_todo(
                normalized,
                source="plan",
                priority=priority,
                dependencies=dependencies,
            )
        )
        if len(todos) >= MAX_TODOS:
            break

    if todos:
        return todos

    fallback_title, priority, dependencies = _normalize_title(
        research_brief,
        priority_hint=1,
    )
    fallback_title = fallback_title or "Complete the approved research brief"
    return [_new_todo(fallback_title, source="plan", priority=priority, dependencies=dependencies)]


def match_todo_for_topic(todos: list[dict[str, Any]], topic: str) -> str | None:
    """Return the best pending/running todo id for a research topic."""
    normalized_topic = _token_set(topic)
    if not normalized_topic:
        return None

    best_id: str | None = None
    best_score = 0.0
    for todo in _valid_todos(todos):
        if todo.get("status") not in {"pending", "running"}:
            continue
        title_tokens = _token_set(str(todo.get("title") or ""))
        if not title_tokens:
            continue
        overlap = len(normalized_topic & title_tokens)
        score = overlap / max(1, min(len(normalized_topic), len(title_tokens)))
        if score > best_score:
            best_score = score
            best_id = str(todo.get("id") or "")

    return best_id if best_score >= 0.2 else None


def ensure_todo_for_topic(
    todos: list[dict[str, Any]],
    topic: str,
    *,
    source: str = "research_architect",
) -> tuple[list[dict[str, Any]], str | None]:
    """Find a matching todo or append a dynamic one for a new research topic."""
    current = _valid_todos(todos)
    matched_id = match_todo_for_topic(current, topic)
    if matched_id:
        return current, matched_id

    title, priority, dependencies = _normalize_title(topic, priority_hint=len(current) + 1)
    if not title:
        return current, None

    existing = {_dedupe_key(str(todo.get("title") or "")) for todo in current}
    key = _dedupe_key(title)
    if key in existing:
        for todo in current:
            if _dedupe_key(str(todo.get("title") or "")) == key:
                return current, str(todo.get("id") or "")
    if len(current) >= MAX_TODOS:
        return current, None

    new_todo = _new_todo(title, source=source, priority=priority, dependencies=dependencies)
    return [*current, new_todo], str(new_todo["id"])


def mark_todo_running(todos: list[dict[str, Any]], todo_id: str | None) -> list[dict[str, Any]]:
    return _update_todo(todos, todo_id, status="running", progress=40, coverage_status="in_progress")


def mark_todo_completed(
    todos: list[dict[str, Any]],
    todo_id: str | None,
    *,
    result_preview: str = "",
) -> list[dict[str, Any]]:
    return _update_todo(
        todos,
        todo_id,
        status="completed",
        progress=100,
        coverage_status="covered",
        result_preview=_preview(result_preview),
    )


def mark_todo_blocked(
    todos: list[dict[str, Any]],
    todo_id: str | None,
    *,
    result_preview: str = "",
) -> list[dict[str, Any]]:
    return _update_todo(
        todos,
        todo_id,
        status="blocked",
        progress=0,
        coverage_status="blocked",
        result_preview=_preview(result_preview),
    )


def append_gap_todos(
    todos: list[dict[str, Any]],
    gaps: list[str],
) -> list[dict[str, Any]]:
    current = _valid_todos(todos)
    existing = {_dedupe_key(str(todo.get("title") or "")) for todo in current}
    output = list(current)
    for gap in gaps:
        title, _, _ = _normalize_title(gap)
        if not title:
            continue
        key = _dedupe_key(title)
        if key in existing:
            continue
        existing.add(key)
        output.append(_new_todo(title, source="gap"))
        if len(output) >= MAX_TODOS:
            break
    return output


def summarize_todos(todos: list[dict[str, Any]]) -> dict[str, Any]:
    current = _valid_todos(todos)
    total = len(current)
    counts = {status: 0 for status in TODO_STATUSES}
    for todo in current:
        status = str(todo.get("status") or "pending")
        counts[status if status in TODO_STATUSES else "pending"] += 1
    completed = counts["completed"]
    progress = round(completed / total * 100) if total else 0
    return {
        "total": total,
        "pending": counts["pending"],
        "running": counts["running"],
        "completed": completed,
        "blocked": counts["blocked"],
        "cancelled": counts["cancelled"],
        "progress_percent": progress,
        "open_titles": [
            str(todo.get("title") or "")
            for todo in current
            if todo.get("status") in {"pending", "running", "blocked"}
        ][:5],
    }


def format_todo_context(todos: list[dict[str, Any]]) -> str:
    summary = summarize_todos(todos)
    if not summary["total"]:
        return ""
    lines = [
        "<research-todo-list>",
        (
            f"Progress: {summary['completed']}/{summary['total']} complete; "
            f"{summary['running']} running, {summary['pending']} pending, "
            f"{summary['blocked']} blocked."
        ),
    ]
    open_todos = [
        todo for todo in _valid_todos(todos)
        if todo.get("status") in {"pending", "running", "blocked"}
    ][:6]
    if open_todos:
        lines.append("Open tasks:")
        for todo in open_todos:
            priority = int(todo.get("priority") or 0)
            dependencies = todo.get("dependencies") or []
            dependency_text = (
                f" deps={', '.join(str(item) for item in dependencies[:3])}"
                if isinstance(dependencies, list) and dependencies
                else ""
            )
            lines.append(
                f"- [{todo.get('status', 'pending')}] p{priority or '?'} "
                f"{todo.get('title', '')}{dependency_text}"
            )
    lines.append(
        "Use this as a soft checklist: prioritize open tasks, add needed angles, "
        "and only call ResearchComplete when the core tasks are covered or explained."
    )
    lines.append("</research-todo-list>")
    return "\n".join(lines)


async def emit_todo_updates(
    thread_id: str,
    todos: list[dict[str, Any]],
    previous: list[dict[str, Any]] | None = None,
) -> None:
    """Emit task_update events for created or changed todos."""
    if not thread_id:
        return
    previous_by_id = {
        str(todo.get("id") or ""): todo
        for todo in _valid_todos(previous or [])
    }
    try:
        from agent.core.events import ToolEvent, get_emitter

        emitter = await get_emitter(thread_id)
        for todo in _valid_todos(todos):
            todo_id = str(todo.get("id") or "")
            old = previous_by_id.get(todo_id)
            if old and _event_fields(old) == _event_fields(todo):
                continue
            await emitter.emit(ToolEvent.TASK_UPDATE, _event_fields(todo))
    except Exception:
        return


def _extract_subtopic_items(plan: str) -> list[str]:
    lines = str(plan or "").splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        heading = re.match(r"^\s{0,3}#{2,6}\s+(.+?)\s*$", line)
        if heading:
            heading_text = re.sub(r"[^a-z0-9]+", "", heading.group(1).lower())
            if heading_text in {"subtopics", "subtopic", "researchtopics", "researchquestions"}:
                in_section = True
                continue
            if in_section:
                break
        if in_section:
            item = _list_item_text(line)
            if item:
                collected.append(item)
    return collected


def _extract_list_items(plan: str) -> list[str]:
    return [
        item
        for line in str(plan or "").splitlines()
        if (item := _list_item_text(line))
    ]


def _list_item_text(line: str) -> str:
    match = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$", line)
    if not match:
        return ""
    return match.group(1)


def _new_todo(
    title: str,
    *,
    source: str,
    priority: int | None = None,
    dependencies: list[str] | None = None,
) -> dict[str, Any]:
    title, derived_priority, derived_dependencies = _normalize_title(title)
    priority = _safe_priority(priority, default=derived_priority)
    dependencies = _clean_dependencies(dependencies) or derived_dependencies
    digest = hashlib.sha1(f"{source}:{title}".encode()).hexdigest()[:10]
    return {
        "id": f"todo_{digest}",
        "title": title,
        "status": "pending",
        "progress": 0,
        "source": source,
        "result_preview": "",
        "priority": priority,
        "dependencies": dependencies,
        "coverage_status": "planned",
        "updated_at": _now(),
    }


def _update_todo(
    todos: list[dict[str, Any]],
    todo_id: str | None,
    *,
    status: str,
    progress: int,
    coverage_status: str | None = None,
    result_preview: str | None = None,
) -> list[dict[str, Any]]:
    if not todo_id:
        return _valid_todos(todos)
    output: list[dict[str, Any]] = []
    for todo in _valid_todos(todos):
        if str(todo.get("id") or "") != str(todo_id):
            output.append(todo)
            continue
        updated = dict(todo)
        updated["status"] = status if status in TODO_STATUSES else "pending"
        updated["progress"] = max(0, min(100, int(progress)))
        if coverage_status:
            updated["coverage_status"] = (
                coverage_status if coverage_status in TODO_COVERAGE_STATUSES else "planned"
            )
        elif status == "completed":
            updated["coverage_status"] = "covered"
        elif status == "running":
            updated["coverage_status"] = "in_progress"
        elif status == "blocked":
            updated["coverage_status"] = "blocked"
        if result_preview is not None:
            updated["result_preview"] = result_preview
        updated["updated_at"] = _now()
        output.append(updated)
    return output


def _valid_todos(todos: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(todos, list):
        return []
    output: list[dict[str, Any]] = []
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        title, _, _ = _normalize_title(str(todo.get("title") or ""))
        if not title:
            continue
        status = str(todo.get("status") or "pending").lower()
        source = str(todo.get("source") or "plan")
        fallback_id = _new_todo(title, source=source)["id"]
        output.append(
            {
                "id": str(todo.get("id") or fallback_id),
                "title": title,
                "status": status if status in TODO_STATUSES else "pending",
                "progress": _safe_progress(todo.get("progress")),
                "source": source,
                "result_preview": _preview(str(todo.get("result_preview") or "")),
                "priority": _safe_priority(todo.get("priority"), default=len(output) + 1),
                "dependencies": _clean_dependencies(todo.get("dependencies")),
                "coverage_status": _safe_coverage_status(
                    todo.get("coverage_status"),
                    status=status if status in TODO_STATUSES else "pending",
                ),
                "updated_at": str(todo.get("updated_at") or _now()),
            }
        )
    return output[:MAX_TODOS]


def _normalize_title(value: str, *, priority_hint: int | None = None) -> tuple[str, int, list[str]]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^\[[ xX]\]\s*", "", text)
    text = text.strip(" -*+\t")
    text = text.replace("**", "").replace("__", "").strip()
    priority = _safe_priority(None, default=max(1, int(priority_hint or 1)))
    dependencies: list[str] = []
    dep_match = re.search(r"\((?:depends?|prereq(?:uisite)?)\s*:\s*([^)]+)\)", text, re.IGNORECASE)
    if dep_match:
        dependencies = _clean_dependencies(dep_match.group(1))
        text = re.sub(r"\((?:depends?|prereq(?:uisite)?)\s*:\s*([^)]+)\)", "", text, flags=re.IGNORECASE).strip()
    priority_match = re.match(r"^\[(?:p|P)?(\d)\]\s*(.+)$", text)
    if priority_match:
        priority = _safe_priority(priority_match.group(1), default=priority)
        text = priority_match.group(2).strip()
    elif re.match(r"^(?:p|P)\d+\s*[:.-]?\s*", text):
        match = re.match(r"^(?:p|P)(\d+)\s*[:.-]?\s*(.+)$", text)
        if match:
            priority = _safe_priority(match.group(1), default=priority)
            text = match.group(2).strip()
    return text[:MAX_TITLE_CHARS].rstrip(), priority, dependencies


def _safe_progress(value: Any) -> int:
    try:
        return max(0, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _dedupe_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _token_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", value.lower())
        if token
    }


def _preview(value: str, max_chars: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars].rstrip()


def _safe_priority(value: Any, *, default: int = 3) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return max(1, min(5, int(default)))


def _clean_dependencies(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;/]+", value)
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        text = text.strip(" -*+\t")
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text[:120])
    return cleaned


def _safe_coverage_status(value: Any, *, status: str = "pending") -> str:
    text = str(value or "").strip().lower()
    if text in TODO_COVERAGE_STATUSES:
        return text
    if status == "completed":
        return "covered"
    if status == "running":
        return "in_progress"
    if status == "blocked":
        return "blocked"
    return "planned"


def _event_fields(todo: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(todo.get("id") or ""),
        "title": str(todo.get("title") or ""),
        "status": str(todo.get("status") or "pending"),
        "progress": int(todo.get("progress") or 0),
        "source": str(todo.get("source") or "plan"),
        "result_preview": str(todo.get("result_preview") or ""),
        "priority": int(todo.get("priority") or 0),
        "dependencies": list(todo.get("dependencies") or []),
        "coverage_status": str(todo.get("coverage_status") or "planned"),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()
