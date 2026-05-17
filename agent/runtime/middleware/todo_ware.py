"""TodoList middleware — enables plan mode task tracking with write_todos tool."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

TODO_SYSTEM_PROMPT = """
<todo_list_system>
You have access to the `write_todos` tool to manage complex multi-step objectives.

CRITICAL RULES:
- Mark todos as completed IMMEDIATELY after finishing each step
- Keep EXACTLY ONE task as `in_progress` at any time
- Update the todo list in REAL-TIME as you work
- DO NOT use for simple tasks (< 3 steps)

Task states: pending, in_progress, completed
</todo_list_system>
"""


@tool("write_todos", parse_docstring=True)
def write_todos(todos: str) -> str:
    """Update the structured task list. Provide a JSON array of {id, status, content}.

    Args:
        todos: JSON string of todo items array.
    """
    try:
        items = json.loads(todos) if isinstance(todos, str) else todos
        return f"Todo list updated: {len(items)} items."
    except Exception as e:
        return f"Error parsing todos: {e}"


class TodoMiddleware(AgentMiddleware):
    """Provides the write_todos tool for plan mode task tracking."""

    def __init__(self):
        super().__init__()

    def before_agent(self, state, runtime) -> dict | None:
        return None

    async def abefore_agent(self, state, runtime) -> dict | None:
        return None
