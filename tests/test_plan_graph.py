from __future__ import annotations


def test_plan_graph_recomputes_ready_frontier_and_todo_projection():
    from agent.workflows.plan_graph import (
        create_plan_graph,
        ready_tasks,
        todos_from_plan_graph,
    )

    graph = create_plan_graph(
        [
            {"id": "a", "title": "Foundation", "status": "completed", "priority": 1},
            {"id": "b", "title": "Dependent evidence", "deps": ["a"], "priority": 2},
            {"id": "c", "title": "Blocked branch", "deps": ["missing"], "priority": 3},
        ]
    )

    assert [task["id"] for task in ready_tasks(graph)] == ["b"]
    todos = todos_from_plan_graph(graph)
    assert todos[0]["status"] == "completed"
    assert todos[1]["status"] == "pending"
    assert todos[1]["dependencies"] == ["a"]


def test_plan_graph_detects_cycles_and_blocks_tasks():
    from agent.workflows.plan_graph import create_plan_graph

    graph = create_plan_graph(
        [
            {"id": "a", "title": "A", "deps": ["b"]},
            {"id": "b", "title": "B", "deps": ["a"]},
        ]
    )

    statuses = {task["id"]: task["status"] for task in graph["tasks"]}
    assert statuses == {"a": "blocked", "b": "blocked"}


def test_replan_actions_add_gap_tasks_and_bump_version():
    from agent.workflows.plan_graph import (
        apply_replan_actions,
        build_gap_replan_actions,
        create_plan_graph,
        ready_tasks,
    )

    graph = create_plan_graph([{"id": "root", "title": "Original", "status": "completed"}])
    updated = apply_replan_actions(
        graph,
        build_gap_replan_actions(["missing primary evidence"]),
        reason="quality gate",
        source="quality_gate",
    )

    assert updated["version"] == graph["version"] + 1
    assert any(task["source"] == "quality_gate" for task in updated["tasks"])
    assert any(event["type"] == "replan_applied" for event in updated["events"])
    assert ready_tasks(updated)


def test_replan_reopens_completed_plan_graph():
    from agent.workflows.plan_graph import apply_replan_actions, create_plan_graph

    graph = create_plan_graph([{"id": "root", "title": "Original", "status": "completed"}])
    assert graph["status"] == "completed"

    updated = apply_replan_actions(
        graph,
        [{"action": "add", "title": "Follow-up", "question": "Verify the follow-up"}],
        reason="quality follow-up",
        source="quality_gate",
    )

    assert updated["status"] == "active"
    assert updated["summary"]["ready"] == 1


def test_plan_graph_from_todos_preserves_dependency_projection():
    from agent.workflows.plan_graph import plan_graph_from_todos, todos_from_plan_graph

    graph = plan_graph_from_todos(
        [
            {"id": "todo_a", "title": "A", "status": "completed"},
            {"id": "todo_b", "title": "B", "dependencies": ["todo_a"]},
        ]
    )

    projected = todos_from_plan_graph(graph)
    assert projected[1]["dependencies"] == ["todo_a"]
    assert projected[1]["coverage_status"] == "planned"
