from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

TODO_STATUSES = {"pending", "running", "completed", "blocked", "cancelled"}
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
        normalized = _normalize_title(title)
        if not normalized:
            continue
        key = _dedupe_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        todos.append(_new_todo(normalized, source="plan"))
        if len(todos) >= MAX_TODOS:
            break

    if todos:
        return todos

    fallback_title = _normalize_title(research_brief) or "Complete the approved research brief"
    return [_new_todo(fallback_title, source="plan")]


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
    source: str = "supervisor",
) -> tuple[list[dict[str, Any]], str | None]:
    """Find a matching todo or append a dynamic one for a new research topic."""
    current = _valid_todos(todos)
    matched_id = match_todo_for_topic(current, topic)
    if matched_id:
        return current, matched_id

    title = _normalize_title(topic)
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

    new_todo = _new_todo(title, source=source)
    return [*current, new_todo], str(new_todo["id"])


def mark_todo_running(todos: list[dict[str, Any]], todo_id: str | None) -> list[dict[str, Any]]:
    return _update_todo(todos, todo_id, status="running", progress=40)


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
        title = _normalize_title(gap)
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
            lines.append(
                f"- [{todo.get('status', 'pending')}] {todo.get('title', '')}"
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


def _new_todo(title: str, *, source: str) -> dict[str, Any]:
    title = _normalize_title(title)
    digest = hashlib.sha1(f"{source}:{title}".encode()).hexdigest()[:10]
    return {
        "id": f"todo_{digest}",
        "title": title,
        "status": "pending",
        "progress": 0,
        "source": source,
        "result_preview": "",
        "updated_at": _now(),
    }


def _update_todo(
    todos: list[dict[str, Any]],
    todo_id: str | None,
    *,
    status: str,
    progress: int,
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
        title = _normalize_title(str(todo.get("title") or ""))
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
                "updated_at": str(todo.get("updated_at") or _now()),
            }
        )
    return output[:MAX_TODOS]


def _normalize_title(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^\[[ xX]\]\s*", "", text)
    text = text.strip(" -*+\t")
    text = text.replace("**", "").replace("__", "").strip()
    return text[:MAX_TITLE_CHARS].rstrip()


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


def _event_fields(todo: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(todo.get("id") or ""),
        "title": str(todo.get("title") or ""),
        "status": str(todo.get("status") or "pending"),
        "progress": int(todo.get("progress") or 0),
        "source": str(todo.get("source") or "plan"),
        "result_preview": str(todo.get("result_preview") or ""),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()
