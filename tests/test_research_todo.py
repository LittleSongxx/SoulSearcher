from __future__ import annotations

from agent.workflows.research_todo import (
    append_gap_todos,
    derive_todos_from_plan,
    ensure_todo_for_topic,
    mark_todo_completed,
    mark_todo_running,
    match_todo_for_topic,
    summarize_todos,
)


def test_derive_todos_prefers_subtopics_section():
    plan = """
# Research Plan

## Context
- This bullet should not become a task.

## Sub-topics
1. Market size and growth drivers
2. Regulatory risks by region
3. Competitive landscape and leading vendors

## Method
- Compare primary and secondary sources.
"""

    todos = derive_todos_from_plan(plan, "Analyze the market")

    assert [todo["title"] for todo in todos] == [
        "Market size and growth drivers",
        "Regulatory risks by region",
        "Competitive landscape and leading vendors",
    ]
    assert all(todo["status"] == "pending" for todo in todos)
    assert all(todo["source"] == "plan" for todo in todos)


def test_derive_todos_uses_revised_plan_content_and_caps_tasks():
    revised_plan = """
## Sub-topics
- Revised scope A
- Revised scope B
- Revised scope A
- Revised scope C
- Revised scope D
- Revised scope E
- Revised scope F
- Revised scope G
- Revised scope H
- Revised scope I
"""

    todos = derive_todos_from_plan(revised_plan, "Original brief")

    assert len(todos) == 8
    assert todos[0]["title"] == "Revised scope A"
    assert todos[1]["title"] == "Revised scope B"
    assert todos[-1]["title"] == "Revised scope H"


def test_derive_todos_fallback_uses_first_six_plan_items():
    plan = """
## Search Strategy
- Fallback item A
- Fallback item B
- Fallback item C
- Fallback item D
- Fallback item E
- Fallback item F
- Fallback item G
"""

    todos = derive_todos_from_plan(plan, "Original brief")

    assert [todo["title"] for todo in todos] == [
        "Fallback item A",
        "Fallback item B",
        "Fallback item C",
        "Fallback item D",
        "Fallback item E",
        "Fallback item F",
    ]


def test_derive_todos_fallback_for_empty_plan():
    todos = derive_todos_from_plan("", "A detailed approved brief")

    assert len(todos) == 1
    assert todos[0]["title"] == "Complete the approved research brief"
    assert todos[0]["status"] == "pending"


def test_match_topic_and_status_summary():
    todos = derive_todos_from_plan(
        """
## Sub-topics
- Battery supply chain constraints
- Policy and regulatory risks
""",
        "",
    )

    matched_id = match_todo_for_topic(todos, "Investigate battery supply constraints")
    assert matched_id == todos[0]["id"]

    todos = mark_todo_running(todos, matched_id)
    todos = mark_todo_completed(
        todos,
        matched_id,
        result_preview="Lithium refining and cell capacity are the major bottlenecks.",
    )
    summary = summarize_todos(todos)

    assert summary["total"] == 2
    assert summary["completed"] == 1
    assert summary["pending"] == 1
    assert summary["progress_percent"] == 50


def test_dynamic_topic_append_and_gap_dedupe():
    todos = derive_todos_from_plan(
        """
## Sub-topics
- Existing supply analysis
""",
        "",
    )

    todos, dynamic_id = ensure_todo_for_topic(
        todos,
        "Customer adoption barriers",
        source="supervisor",
    )
    assert dynamic_id
    assert todos[-1]["source"] == "supervisor"

    todos = append_gap_todos(
        todos,
        [
            "Customer adoption barriers",
            "Evidence gap: pricing sensitivity by segment",
            "Evidence gap: pricing sensitivity by segment",
        ],
    )

    titles = [todo["title"] for todo in todos]
    assert titles.count("Customer adoption barriers") == 1
    assert titles.count("Evidence gap: pricing sensitivity by segment") == 1
    assert summarize_todos(todos)["pending"] == 3


def test_evidence_store_response_patch_includes_todos():
    from common.evidence_store import build_evidence_store_snapshot

    todos = derive_todos_from_plan(
        """
## Sub-topics
- Evidence mapping
""",
        "",
    )
    summary = summarize_todos(todos)

    snapshot = build_evidence_store_snapshot(
        thread_id="thread_1",
        artifacts={
            "research_todos": todos,
            "todo_summary": summary,
        },
    )
    patch = snapshot.to_response_patch()

    assert patch["research_todos"] == todos
    assert patch["todo_summary"] == summary
