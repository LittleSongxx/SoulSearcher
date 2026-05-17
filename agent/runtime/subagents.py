"""DeerFlow-aligned subagent execution engine.

Features:
- Dual thread pool (scheduler + isolated persistent event loop)
- Independent context per subagent with configurable tool whitelist
- Skill loading per subagent session
- Cooperative cancellation via cancel_event
- Real-time event streaming during execution
- Timeout enforcement at both thread-pool and polling levels
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.runtime.context import RuntimeContext

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SUBAGENTS = 3

# ── Thread pools ──
_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-sched-")

_isolated_loop: asyncio.AbstractEventLoop | None = None
_isolated_loop_thread: threading.Thread | None = None
_isolated_loop_lock = threading.Lock()


def _run_isolated_loop(loop: asyncio.AbstractEventLoop, started: threading.Event) -> None:
    asyncio.set_event_loop(loop)
    loop.call_soon(started.set)
    try:
        loop.run_forever()
    finally:
        started.clear()


def _get_isolated_loop() -> asyncio.AbstractEventLoop:
    global _isolated_loop, _isolated_loop_thread
    with _isolated_loop_lock:
        alive = _isolated_loop_thread is not None and _isolated_loop_thread.is_alive()
        usable = _isolated_loop is not None and not _isolated_loop.is_closed() and _isolated_loop.is_running() and alive
        if not usable:
            loop = asyncio.new_event_loop()
            started = threading.Event()
            thread = threading.Thread(target=_run_isolated_loop, args=(loop, started), name="subagent-loop", daemon=True)
            thread.start()
            if not started.wait(timeout=5):
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1)
                loop.close()
                raise RuntimeError("Timed out starting isolated subagent loop")
            _isolated_loop = loop
            _isolated_loop_thread = thread
        if _isolated_loop is None:
            raise RuntimeError("Isolated subagent loop not initialized")
        return _isolated_loop


def _shutdown_isolated_loop() -> None:
    global _isolated_loop, _isolated_loop_thread
    with _isolated_loop_lock:
        loop = _isolated_loop
        thread = _isolated_loop_thread
        _isolated_loop = None
        _isolated_loop_thread = None
    if loop is None:
        return
    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)
    if not loop.is_closed():
        try:
            loop.close()
        except Exception:
            pass


atexit.register(_shutdown_isolated_loop)


# ── Data types ──

class SubagentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class SubagentConfig:
    name: str
    description: str = ""
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    skills: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900


@dataclass
class SubagentResult:
    task_id: str
    trace_id: str
    status: SubagentStatus = SubagentStatus.PENDING
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)
    usage_reported: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def __post_init__(self):
        if self.ai_messages is None:
            self.ai_messages = []


# ── Built-in subagents ──

BUILTIN_SUBAGENTS: dict[str, SubagentConfig] = {
    "general-purpose": SubagentConfig(
        name="general-purpose",
        description="For ANY non-trivial task — web research, code exploration, file operations, analysis, etc.",
        system_prompt="You are a focused Weaver subagent. Complete the delegated task using available tools. Return concise, actionable results.",
        timeout_seconds=300,
        max_turns=40,
    ),
    "researcher": SubagentConfig(
        name="researcher",
        description="Gather evidence from multiple sources with citations. Use for parallel research branches.",
        system_prompt="You are a research subagent. Gather evidence from multiple angles, prefer authoritative sources, and return concise findings with citations.",
        tools=["tavily_search", "fallback_search", "crawl_url", "crawl_urls"],
        timeout_seconds=420,
        max_turns=50,
    ),
    "coder": SubagentConfig(
        name="coder",
        description="For coding, file operations, and sandbox command execution.",
        system_prompt="You are a coding subagent. Prefer sandbox tools. Do not use host shell unless explicitly allowed.",
        tools=["sandbox_execute_command", "sandbox_read_file", "sandbox_update_file", "sandbox_create_file"],
        timeout_seconds=420,
        max_turns=50,
    ),
}


# ── Registry ──

def get_subagent_config(name: str) -> SubagentConfig | None:
    normalized = (name or "general-purpose").strip().lower()
    return BUILTIN_SUBAGENTS.get(normalized)


def get_available_subagent_names() -> list[str]:
    return sorted(BUILTIN_SUBAGENTS)


# ── Background task storage ──

_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()


def get_background_task_result(task_id: str) -> SubagentResult | None:
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def cleanup_background_task(task_id: str) -> None:
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is not None and result.status in {
            SubagentStatus.COMPLETED, SubagentStatus.FAILED,
            SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT,
        }:
            del _background_tasks[task_id]


def request_cancel_background_task(task_id: str) -> bool:
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            return False
        result.cancel_event.set()
        return True


# ── Subagent Executor ──

class SubagentExecutor:
    """Executes subagents with independent context and tools."""

    def __init__(
        self,
        config: SubagentConfig,
        tools: list | None = None,
        parent_model: str | None = None,
        runtime_context: RuntimeContext | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
    ):
        self.config = config
        self._base_tools = tools or []
        self.parent_model = parent_model
        self.runtime_context = runtime_context or RuntimeContext()
        self.thread_id = thread_id
        self.trace_id = trace_id or uuid.uuid4().hex[:8]

        # Filter tools
        self.tools = self._filter_tools(self._base_tools)

        logger.info("[trace=%s] SubagentExecutor: %s with %d tools", self.trace_id, config.name, len(self.tools))

    def _filter_tools(self, all_tools: list) -> list:
        from langchain_core.tools import BaseTool
        filtered = list(all_tools)

        if self.config.tools is not None:
            allowed = set(self.config.tools)
            filtered = [t for t in filtered if getattr(t, "name", "") in allowed]

        if self.config.disallowed_tools is not None:
            disallowed = set(self.config.disallowed_tools)
            filtered = [t for t in filtered if getattr(t, "name", "") not in disallowed]

        return filtered

    async def _load_skills(self) -> list:
        """Load enabled skills based on config.skills whitelist."""
        if self.config.skills is not None and len(self.config.skills) == 0:
            return []

        try:
            from agent.skills.storage import get_or_new_skill_storage
            storage = await asyncio.to_thread(get_or_new_skill_storage)
            all_skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
        except Exception:
            logger.exception("[trace=%s] Failed to load skills", self.trace_id)
            return []

        if not all_skills:
            return []

        if self.config.skills is not None:
            allowed = set(self.config.skills)
            return [s for s in all_skills if s.name in allowed]
        return all_skills

    async def _build_initial_state(self, task: str) -> tuple[dict[str, Any], list]:
        skills = await self._load_skills()

        # Apply skill allowed-tools filtering
        from agent.skills.tool_policy import filter_tools_by_skill_allowed_tools
        filtered_tools = filter_tools_by_skill_allowed_tools(self.tools, skills)

        # Build system prompt with progressive-loading skills section
        system_parts: list[str] = []
        if self.config.system_prompt:
            system_parts.append(self.config.system_prompt)

        # Inject available skills list (progressive loading: agent reads SKILL.md on demand)
        if skills:
            try:
                from agent.skills.prompt import get_skills_prompt_section
                available_names = {s.name for s in skills}
                container_path = self.config.container_path if hasattr(self.config, "container_path") else "/mnt/skills"
                skills_section = get_skills_prompt_section(available_skills=available_names, container_base_path=container_path)
                if skills_section:
                    system_parts.append(skills_section)
            except Exception:
                logger.debug("[trace=%s] Failed to build skills prompt section", self.trace_id)

        messages: list = []
        if system_parts:
            messages.append(SystemMessage(content="\n\n".join(system_parts)))
        messages.append(HumanMessage(content=task))

        state = {"messages": messages}
        if self.runtime_context:
            state["thread_id"] = self.runtime_context.thread_id
            state["user_id"] = self.runtime_context.user_id

        return state, filtered_tools

    async def _aexecute(self, task: str, result: SubagentResult) -> SubagentResult:
        from agent.workflows.agent_factory import build_tool_agent
        from common.config import settings

        result.status = SubagentStatus.RUNNING
        result.started_at = datetime.utcnow()

        try:
            if result.cancel_event.is_set():
                raise RuntimeError("Cancelled before start")

            state, filtered_tools = await self._build_initial_state(task)
            agent = build_tool_agent(
                model=self.parent_model or settings.primary_model,
                tools=filtered_tools,
                temperature=0.4,
            )

            run_config: RunnableConfig = {
                "recursion_limit": self.config.max_turns,
            }
            if self.thread_id:
                run_config["configurable"] = {"thread_id": self.thread_id}

            final_state = None
            async for chunk in agent.astream(state, config=run_config, stream_mode="values"):
                if result.cancel_event.is_set():
                    result.status = SubagentStatus.CANCELLED
                    result.error = "Cancelled by user"
                    result.completed_at = datetime.utcnow()
                    return result
                final_state = chunk

                messages = chunk.get("messages", [])
                if messages and isinstance(messages[-1], AIMessage):
                    msg_dict = messages[-1].model_dump()
                    if msg_dict not in (result.ai_messages or []):
                        (result.ai_messages or []).append(msg_dict)

            if final_state is None:
                result.result = "No response generated"
            else:
                last_msgs = final_state.get("messages", [])
                for msg in reversed(last_msgs):
                    if isinstance(msg, AIMessage):
                        content = msg.content
                        result.result = content if isinstance(content, str) else str(content)
                        break

            result.status = SubagentStatus.COMPLETED
        except Exception as exc:
            logger.exception("[trace=%s] Subagent %s failed", self.trace_id, self.config.name)
            if result.cancel_event.is_set():
                result.status = SubagentStatus.CANCELLED
                result.error = "Cancelled by user"
            else:
                result.status = SubagentStatus.FAILED
                result.error = str(exc)
        finally:
            result.completed_at = datetime.utcnow()

        return result

    def execute(self, task: str) -> SubagentResult:
        """Synchronous execution — creates event loop if needed."""
        result = SubagentResult(
            task_id=uuid.uuid4().hex[:12],
            trace_id=self.trace_id,
        )
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._aexecute(task, result), _get_isolated_loop()
                )
                return future.result(timeout=self.config.timeout_seconds)
            else:
                return asyncio.run(self._aexecute(task, result))
        except FuturesTimeoutError:
            result.cancel_event.set()
            result.status = SubagentStatus.TIMED_OUT
            result.error = f"Timed out after {self.config.timeout_seconds}s"
            result.completed_at = datetime.utcnow()
            return result
        except Exception as exc:
            result.status = SubagentStatus.FAILED
            result.error = str(exc)
            result.completed_at = datetime.utcnow()
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """Start background execution, return task_id for polling."""
        task_id = task_id or uuid.uuid4().hex[:12]
        result = SubagentResult(task_id=task_id, trace_id=self.trace_id, status=SubagentStatus.PENDING)

        with _background_tasks_lock:
            _background_tasks[task_id] = result

        parent_context = copy_context()

        def _run():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING
                _background_tasks[task_id].started_at = datetime.utcnow()
                holder = _background_tasks[task_id]

            try:
                future = asyncio.run_coroutine_threadsafe(
                    parent_context.run(lambda: self._aexecute(task, holder)),
                    _get_isolated_loop(),
                )
                exec_result = future.result(timeout=self.config.timeout_seconds)
                with _background_tasks_lock:
                    _background_tasks[task_id].status = exec_result.status
                    _background_tasks[task_id].result = exec_result.result
                    _background_tasks[task_id].error = exec_result.error
                    _background_tasks[task_id].completed_at = exec_result.completed_at
                    _background_tasks[task_id].ai_messages = exec_result.ai_messages
            except FuturesTimeoutError:
                holder.cancel_event.set()
                with _background_tasks_lock:
                    if _background_tasks[task_id].status == SubagentStatus.RUNNING:
                        _background_tasks[task_id].status = SubagentStatus.TIMED_OUT
                        _background_tasks[task_id].error = f"Timed out after {self.config.timeout_seconds}s"
                        _background_tasks[task_id].completed_at = datetime.utcnow()
                future.cancel() if 'future' in dir() else None
            except Exception as exc:
                logger.exception("[trace=%s] Async subagent failed", self.trace_id)
                with _background_tasks_lock:
                    _background_tasks[task_id].status = SubagentStatus.FAILED
                    _background_tasks[task_id].error = str(exc)
                    _background_tasks[task_id].completed_at = datetime.utcnow()

        _scheduler_pool.submit(_run)
        return task_id
