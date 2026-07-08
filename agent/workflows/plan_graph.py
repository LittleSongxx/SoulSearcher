from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

PLAN_GRAPH_SCHEMA_VERSION = 1

TASK_STATUSES = {
    "pending",
    "ready",
    "running",
    "completed",
    "blocked",
    "cancelled",
    "retired",
    "needs_followup",
}

REPLAN_ACTIONS = {
    "split",
    "add",
    "merge",
    "retire",
    "change_deps",
    "escalate",
    "request_human",
    "finish",
}


def create_plan_graph(
    tasks: list[dict[str, Any]] | None = None,
    *,
    source: str = "plan",
    version: int = 1,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    graph = {
        "schema_version": PLAN_GRAPH_SCHEMA_VERSION,
        "version": max(1, int(version or 1)),
        "status": "active",
        "tasks": [],
        "events": [],
        "frontier": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    normalized: list[dict[str, Any]] = []
    for index, task in enumerate(tasks or [], start=1):
        normalized_task = normalize_task(task, source=source, priority=index)
        if normalized_task:
            normalized.append(normalized_task)
    graph["tasks"] = _dedupe_tasks(normalized)
    graph["events"] = list(events or [])
    if graph["tasks"] and not graph["events"]:
        graph["events"].append(_event("plan_created", {"task_count": len(graph["tasks"])}))
    return recompute_plan_graph(graph)


def plan_graph_from_todos(
    todos: list[dict[str, Any]] | None,
    *,
    source: str = "todo",
) -> dict[str, Any]:
    tasks = []
    for index, todo in enumerate(todos or [], start=1):
        if not isinstance(todo, dict):
            continue
        tasks.append(
            {
                "id": str(todo.get("id") or ""),
                "title": str(todo.get("title") or ""),
                "question": str(todo.get("title") or ""),
                "deps": list(todo.get("dependencies") or []),
                "status": _status_from_todo(todo),
                "priority": int(todo.get("priority") or index),
                "source": str(todo.get("source") or source),
                "result_preview": str(todo.get("result_preview") or ""),
                "updated_at": str(todo.get("updated_at") or _now()),
            }
        )
    return create_plan_graph(tasks, source=source)


def ensure_plan_graph(
    value: Any,
    *,
    fallback_todos: list[dict[str, Any]] | None = None,
    source: str = "plan",
) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("tasks"), list):
        graph = dict(value)
        graph.setdefault("schema_version", PLAN_GRAPH_SCHEMA_VERSION)
        graph.setdefault("version", 1)
        graph.setdefault("status", "active")
        graph.setdefault("events", [])
        graph["tasks"] = [
            task
            for task in (
                normalize_task(item, source=str(item.get("source") or source), priority=index)
                for index, item in enumerate(graph.get("tasks") or [], start=1)
                if isinstance(item, dict)
            )
            if task
        ]
        return recompute_plan_graph(graph)
    return plan_graph_from_todos(fallback_todos or [], source=source)


def normalize_task(
    task: dict[str, Any],
    *,
    source: str = "plan",
    priority: int = 3,
) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    title = _clean_text(task.get("title") or task.get("question") or task.get("topic"))
    question = _clean_text(task.get("question") or title)
    if not title and not question:
        return {}
    title = title or question
    question = question or title
    task_id = _clean_text(task.get("id"))
    if not task_id:
        task_id = stable_task_id(title, source=source)
    status = str(task.get("status") or "pending").strip().lower()
    if status not in TASK_STATUSES:
        status = "pending"
    deps = _clean_id_list(task.get("deps") or task.get("dependencies"))
    now = _now()
    retrieval_policy_hint = (
        task.get("retrieval_policy_hint")
        if isinstance(task.get("retrieval_policy_hint"), dict)
        else {}
    )
    budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return {
        "id": task_id,
        "title": title[:180],
        "question": question[:500],
        "deps": deps,
        "status": status,
        "priority": _safe_int(task.get("priority"), default=priority, minimum=1, maximum=9),
        "retrieval_policy_hint": dict(retrieval_policy_hint),
        "budget": dict(budget),
        "evidence_ids": _clean_id_list(task.get("evidence_ids")),
        "claim_ids": _clean_id_list(task.get("claim_ids")),
        "blocked_reason": _clean_text(task.get("blocked_reason")),
        "attempts": _safe_int(task.get("attempts"), default=0, minimum=0, maximum=99),
        "source": _clean_text(task.get("source") or source),
        "created_at": _clean_text(task.get("created_at")) or now,
        "updated_at": _clean_text(task.get("updated_at")) or now,
        "result_preview": _clean_text(task.get("result_preview"))[:260],
        "metadata": metadata,
    }


def recompute_plan_graph(graph: dict[str, Any]) -> dict[str, Any]:
    graph = dict(graph or {})
    tasks = _dedupe_tasks([task for task in graph.get("tasks", []) if isinstance(task, dict)])
    task_ids = {task["id"] for task in tasks}
    completed = {
        task["id"]
        for task in tasks
        if task.get("status") in {"completed", "retired", "cancelled"}
    }
    for task in tasks:
        deps = list(task.get("deps") or [])
        missing = [dep for dep in deps if dep not in task_ids]
        task["deps"] = [dep for dep in deps if dep in task_ids and dep != task["id"]]
        if missing and task.get("status") not in {"completed", "retired", "cancelled"}:
            task["status"] = "blocked"
            task["blocked_reason"] = f"Missing dependencies: {', '.join(missing[:3])}"
    cycles = detect_cycles(tasks)
    if cycles:
        for task in tasks:
            if task["id"] in cycles:
                task["status"] = "blocked"
                task["blocked_reason"] = "Dependency cycle detected."
    frontier: list[str] = []
    for task in sorted(tasks, key=lambda item: (int(item.get("priority") or 9), item.get("created_at") or "")):
        if task.get("status") not in {"pending", "ready", "needs_followup"}:
            continue
        deps = set(task.get("deps") or [])
        if deps.issubset(completed):
            task["status"] = "ready"
            frontier.append(task["id"])
    total = len(tasks)
    done = len([task for task in tasks if task.get("status") in {"completed", "retired", "cancelled"}])
    blocked = len([task for task in tasks if task.get("status") == "blocked"])
    running = len([task for task in tasks if task.get("status") == "running"])
    graph["tasks"] = tasks
    graph["frontier"] = frontier
    graph["summary"] = {
        "total": total,
        "ready": len(frontier),
        "running": running,
        "completed": done,
        "blocked": blocked,
        "progress_percent": round(done / total * 100) if total else 0,
    }
    graph["status"] = (
        "completed" if total and done == total else
        "blocked" if total and blocked and not frontier and not running else
        "active"
    )
    graph["updated_at"] = _now()
    graph.setdefault("events", [])
    graph.setdefault("version", 1)
    graph.setdefault("schema_version", PLAN_GRAPH_SCHEMA_VERSION)
    return graph


def detect_cycles(tasks: list[dict[str, Any]]) -> set[str]:
    deps_by_id = {
        str(task.get("id")): [str(dep) for dep in (task.get("deps") or [])]
        for task in tasks
        if task.get("id")
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            cyclic.update(visiting)
            cyclic.add(task_id)
            return
        visiting.add(task_id)
        for dep in deps_by_id.get(task_id, []):
            if dep in deps_by_id:
                visit(dep)
        visiting.discard(task_id)
        visited.add(task_id)

    for task_id in deps_by_id:
        visit(task_id)
    return cyclic


def ready_tasks(graph: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    graph = recompute_plan_graph(graph)
    frontier = set(graph.get("frontier") or [])
    tasks = [
        task
        for task in graph.get("tasks", [])
        if isinstance(task, dict) and task.get("id") in frontier
    ]
    tasks.sort(key=lambda item: (int(item.get("priority") or 9), item.get("created_at") or ""))
    return tasks[: max(0, int(limit or 0))]


def mark_task_running(graph: dict[str, Any], task_id: str) -> dict[str, Any]:
    return update_task(graph, task_id, status="running", attempts_increment=1)


def mark_task_completed(
    graph: dict[str, Any],
    task_id: str,
    *,
    result_preview: str = "",
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return update_task(
        graph,
        task_id,
        status="completed",
        result_preview=result_preview,
        evidence_ids=evidence_ids,
    )


def mark_task_blocked(
    graph: dict[str, Any],
    task_id: str,
    *,
    blocked_reason: str = "",
    result_preview: str = "",
) -> dict[str, Any]:
    return update_task(
        graph,
        task_id,
        status="blocked",
        blocked_reason=blocked_reason,
        result_preview=result_preview,
    )


def update_task(
    graph: dict[str, Any],
    task_id: str,
    *,
    status: str | None = None,
    attempts_increment: int = 0,
    result_preview: str = "",
    evidence_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    blocked_reason: str = "",
) -> dict[str, Any]:
    graph = ensure_plan_graph(graph)
    task_id = str(task_id or "")
    for task in graph.get("tasks", []):
        if task.get("id") != task_id:
            continue
        old_status = task.get("status")
        if status:
            task["status"] = status if status in TASK_STATUSES else old_status
        if attempts_increment:
            task["attempts"] = _safe_int(task.get("attempts"), default=0) + int(attempts_increment)
        if result_preview:
            task["result_preview"] = _clean_text(result_preview)[:260]
        if evidence_ids:
            task["evidence_ids"] = _merge_ids(task.get("evidence_ids"), evidence_ids)
        if claim_ids:
            task["claim_ids"] = _merge_ids(task.get("claim_ids"), claim_ids)
        if blocked_reason:
            task["blocked_reason"] = _clean_text(blocked_reason)
        elif status and status != "blocked":
            task["blocked_reason"] = ""
        task["updated_at"] = _now()
        if status and status != old_status:
            graph = append_plan_event(
                graph,
                "task_status_changed",
                {"task_id": task_id, "from": old_status, "to": status},
                bump_version=False,
            )
        break
    return recompute_plan_graph(graph)


def apply_replan_actions(
    graph: dict[str, Any],
    actions: list[dict[str, Any]],
    *,
    reason: str = "",
    source: str = "replan",
) -> dict[str, Any]:
    graph = ensure_plan_graph(graph)
    applied: list[dict[str, Any]] = []
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action") or action.get("type") or "").strip().lower()
        if action_type not in REPLAN_ACTIONS:
            continue
        if action_type == "add":
            task = normalize_task(
                {
                    **action,
                    "title": action.get("title") or action.get("question"),
                    "question": action.get("question") or action.get("title"),
                    "deps": action.get("deps") or action.get("dependencies") or [],
                    "status": "pending",
                    "source": source,
                },
                source=source,
                priority=len(graph.get("tasks", [])) + 1,
            )
            if task and not _has_task(graph, task["id"], task["title"]):
                graph["tasks"].append(task)
                applied.append({"action": "add", "task_id": task["id"], "title": task["title"]})
        elif action_type == "retire":
            task_id = _clean_text(action.get("task_id") or action.get("id"))
            if task_id:
                graph = update_task(graph, task_id, status="retired", result_preview=_clean_text(action.get("reason")))
                applied.append({"action": "retire", "task_id": task_id})
        elif action_type == "escalate":
            task_id = _clean_text(action.get("task_id") or action.get("id"))
            for task in graph.get("tasks", []):
                if task.get("id") == task_id:
                    budget = dict(task.get("budget") or {})
                    budget["research_effort"] = "thorough"
                    budget["escalated"] = True
                    task["budget"] = budget
                    task["status"] = "pending" if task.get("status") == "blocked" else task.get("status")
                    task["updated_at"] = _now()
                    applied.append({"action": "escalate", "task_id": task_id})
                    break
        elif action_type == "change_deps":
            task_id = _clean_text(action.get("task_id") or action.get("id"))
            deps = _clean_id_list(action.get("deps") or action.get("dependencies"))
            for task in graph.get("tasks", []):
                if task.get("id") == task_id:
                    task["deps"] = deps
                    task["updated_at"] = _now()
                    applied.append({"action": "change_deps", "task_id": task_id})
                    break
        elif action_type in {"split", "merge", "request_human", "finish"}:
            applied.append({"action": action_type, "reason": _clean_text(action.get("reason") or reason)})
    if applied:
        graph = append_plan_event(
            graph,
            "replan_applied",
            {"reason": reason, "actions": applied},
            bump_version=True,
        )
    return recompute_plan_graph(graph)


def build_gap_replan_actions(
    gaps: list[str],
    *,
    depends_on: list[str] | None = None,
    source: str = "quality_gate",
) -> list[dict[str, Any]]:
    actions = []
    seen: set[str] = set()
    for gap in gaps:
        text = _clean_text(gap)
        if not text:
            continue
        key = _dedupe_key(text)
        if key in seen:
            continue
        seen.add(key)
        actions.append(
            {
                "action": "add",
                "title": f"Resolve evidence gap: {text[:140]}",
                "question": (
                    "Gather current, source-backed evidence that resolves this "
                    f"research gap: {text}"
                ),
                "deps": list(depends_on or []),
                "source": source,
                "priority": 1,
                "retrieval_policy_hint": {
                    "profiles": ["academic"],
                    "methods": ["web_search", "academic_search", "crawl", "deep_read"],
                },
                "budget": {"research_effort": "thorough", "purpose": "verification"},
            }
        )
    return actions


def todos_from_plan_graph(graph: dict[str, Any]) -> list[dict[str, Any]]:
    graph = ensure_plan_graph(graph)
    todos = []
    for task in graph.get("tasks", []):
        status = str(task.get("status") or "pending")
        todo_status = {
            "ready": "pending",
            "needs_followup": "pending",
            "retired": "cancelled",
        }.get(status, status if status in {"pending", "running", "completed", "blocked", "cancelled"} else "pending")
        progress = 100 if todo_status == "completed" else 45 if todo_status == "running" else 0
        todos.append(
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": todo_status,
                "progress": progress,
                "source": str(task.get("source") or "plan"),
                "result_preview": str(task.get("result_preview") or task.get("blocked_reason") or ""),
                "priority": int(task.get("priority") or 3),
                "dependencies": list(task.get("deps") or []),
                "coverage_status": (
                    "covered" if todo_status == "completed" else
                    "in_progress" if todo_status == "running" else
                    "blocked" if todo_status == "blocked" else
                    "planned"
                ),
                "updated_at": str(task.get("updated_at") or _now()),
            }
        )
    return todos


def summarize_plan_graph(graph: dict[str, Any]) -> dict[str, Any]:
    graph = recompute_plan_graph(ensure_plan_graph(graph))
    summary = dict(graph.get("summary") or {})
    summary["version"] = int(graph.get("version") or 1)
    summary["frontier"] = list(graph.get("frontier") or [])[:10]
    summary["status"] = str(graph.get("status") or "active")
    return summary


def append_plan_event(
    graph: dict[str, Any],
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    bump_version: bool = False,
) -> dict[str, Any]:
    graph = dict(graph or {})
    events = list(graph.get("events") or [])
    event = _event(event_type, payload or {}, version=int(graph.get("version") or 1))
    events.append(event)
    graph["events"] = events[-200:]
    if bump_version:
        graph["version"] = int(graph.get("version") or 1) + 1
        graph["updated_at"] = _now()
    return graph


def task_context_for_architect(graph: dict[str, Any], *, max_ready: int = 5) -> str:
    graph = recompute_plan_graph(ensure_plan_graph(graph))
    ready = ready_tasks(graph, limit=max_ready)
    if not graph.get("tasks"):
        return ""
    lines = [
        "<plan-graph>",
        (
            f"Version: {graph.get('version', 1)}; "
            f"status={graph.get('status', 'active')}; "
            f"ready={len(ready)}; completed={graph.get('summary', {}).get('completed', 0)}/"
            f"{graph.get('summary', {}).get('total', 0)}."
        ),
    ]
    if ready:
        lines.append("Ready tasks to dispatch now:")
        for task in ready:
            lines.append(
                f"- task_id={task['id']} p{task.get('priority', 3)} "
                f"title={task.get('title', '')} question={task.get('question', '')}"
            )
    blocked = [task for task in graph.get("tasks", []) if task.get("status") == "blocked"][:3]
    if blocked:
        lines.append("Blocked tasks:")
        for task in blocked:
            lines.append(f"- task_id={task['id']} {task.get('title', '')}: {task.get('blocked_reason', '')}")
    lines.append(
        "Assign ready tasks to the fixed-role pipeline unless a newly discovered gap must be added first."
    )
    lines.append("</plan-graph>")
    return "\n".join(lines)


def _dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for task in tasks:
        task_id = str(task.get("id") or "")
        title_key = _dedupe_key(str(task.get("title") or ""))
        if not task_id or task_id in seen_ids or title_key in seen_titles:
            continue
        seen_ids.add(task_id)
        if title_key:
            seen_titles.add(title_key)
        output.append(task)
    return output[:24]


def _has_task(graph: dict[str, Any], task_id: str, title: str) -> bool:
    title_key = _dedupe_key(title)
    for task in graph.get("tasks", []):
        if task.get("id") == task_id:
            return True
        if title_key and _dedupe_key(str(task.get("title") or "")) == title_key:
            return True
    return False


def stable_task_id(title: str, *, source: str = "plan") -> str:
    digest = hashlib.sha1(f"{source}:{_dedupe_key(title)}".encode()).hexdigest()[:12]
    return f"pt_{digest}"


def _event(event_type: str, payload: dict[str, Any], *, version: int = 1) -> dict[str, Any]:
    return {
        "event_id": f"pe_{hashlib.sha1(f'{event_type}:{_now()}:{payload}'.encode()).hexdigest()[:12]}",
        "type": str(event_type or "plan_event"),
        "version": int(version or 1),
        "payload": dict(payload or {}),
        "created_at": _now(),
    }


def _status_from_todo(todo: dict[str, Any]) -> str:
    status = str(todo.get("status") or "pending").strip().lower()
    if status == "completed":
        return "completed"
    if status == "running":
        return "running"
    if status == "blocked":
        return "blocked"
    if status == "cancelled":
        return "cancelled"
    return "pending"


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_id_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;/]+", value)
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text[:140])
    return output


def _merge_ids(current: Any, additions: list[str]) -> list[str]:
    return _clean_id_list([*_clean_id_list(current), *_clean_id_list(additions)])


def _safe_int(value: Any, *, default: int = 0, minimum: int = 0, maximum: int = 999) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(minimum, min(maximum, number))


def _dedupe_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def _now() -> str:
    return datetime.now(UTC).isoformat()
