# AGENTS.md — AI Agent Guidance for Weaver

## Project Overview

Weaver is a LangGraph-based AI super agent platform with DeerFlow-aligned architecture. The
backend provides a Lead Agent with subagent delegation, persistent memory, skills system,
MCP integration, sandbox isolation, and multi-IM channel support.

## Architecture

```
agent/runtime/         # DeerFlow-aligned runtime harness
  lead_agent.py        # Lead agent factory with 12-layer middleware chain
  subagents.py         # Subagent executor (dual thread pool + isolated event loop)
  task_tool.py         # task() delegation tool with real-time SSE events
  tool_registry.py     # Unified tool assembly (built-in + MCP + community + subagent)
  tool_search.py       # Deferred MCP tool discovery (DeferredToolRegistry)
  context.py           # RuntimeContext dataclass
  state.py             # ThreadState schema (AgentState + sandbox + artifacts + todos)
  mcp_cache.py         # MCP tool cache with mtime-based staleness detection
  sandbox_policy.py    # Host bash security gating, command auditing
  sandbox_provider.py  # SandboxProvider ABC (E2B/Daytona implementations)
  guardrails.py        # Pre-tool-call authorization (AllowlistProvider + protocol)
  user_context.py      # Per-request user_id resolution via ContextVar
  deep_research_tool.py  # DeepSearch wrapped as LangChain tool
  middleware/           # 12 middleware components (see below)
  memory/              # Long-term memory system (LLM extraction + debounced queue)

agent/skills/          # Skills system (SKILL.md parsing + tool allowlisting)
agent/mcp/             # MCP OAuth + interceptor
agent/core/            # Core graph, state, LLM factory, events
agent/workflows/       # DeepSearch pipeline, agent tools, coordinator

channels/              # IM channel integration (Feishu, Slack, Telegram, etc.)
common/                # Shared utilities (config, rate limiter, stream registry)
tools/                 # Tool implementations (sandbox, MCP, automation, etc.)
```

## Middleware Chain (strict order)

1. ThreadDataMiddleware — per-thread isolation directories
2. UploadsMiddleware — uploaded file injection
3. DynamicContextMiddleware — `<system-reminder>` (memory + current date)
4. SummarizationMiddleware — context reduction at token limits
5. TodoMiddleware — plan mode task tracking
6. TitleMiddleware — auto thread title generation
7. MemoryMiddleware — enqueue conversation for async memory update
8. SubagentLimitMiddleware — truncate excess task() calls
9. LoopDetectionMiddleware — break repetitive tool-call loops
10. ToolErrorHandlingMiddleware — tool exceptions → ToolMessages
11. SandboxAuditMiddleware — security audit logging
12. ClarificationMiddleware — intercept ask_clarification → interrupt (MUST be last)

## Key Design Decisions

- **Static system prompt**: Identical across users/sessions for maximum prefix-cache reuse.
  Dynamic content (date, memory) injected via DynamicContextMiddleware as hidden HumanMessages.
- **Subagent isolation**: Each subagent runs with independent context, filtered tools,
  and per-session skill loading. task() tool is disallowed in subagents to prevent recursion.
- **Memory flow**: MemoryMiddleware → MemoryUpdateQueue (debounce 30s, dedup) →
  MemoryUpdater (sync model.invoke, atomic file write) → next-turn injection.
- **Deferred tools**: MCP tools hidden behind tool_search. Agent sees names only in
  `<available-deferred-tools>`; schema fetched on demand and promoted to active.

## Development Commands

```bash
# Start Weaver
./start_weaver.sh

# Run tests (when test suite is re-established)
PYTHONPATH=. python -m pytest tests/ -v

# Syntax check
python -c "import py_compile; ..."
```

## File Conventions

- `agent/runtime/` imports from `agent.workflows`, `agent.skills`, `common`, `tools` — never the reverse
- `agent/skills/` is independent of `agent/runtime/` (can be loaded standalone)
- `channels/` imports from `agent.runtime`, `common` — never the reverse
- `common/config.py` is the single source of truth for all Pydantic Settings fields
- New config fields should be added to `common/config.py` with sensible defaults

## Git Workflow

- Check current branch and working tree before changing files
- Preserve unrelated local changes
- Run syntax checks before handoff
- Do NOT commit secrets (.env, credentials.json)
- Commit messages in English, present tense
