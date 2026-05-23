# AGENTS.md — AI Agent Guidance for Weaver

## Project Overview

Weaver is a LangGraph-based AI deep research agent platform. It decomposes complex research
tasks into a multi-stage graph pipeline: clarify → plan → supervisor → N parallel researchers
→ report. Every stage uses typed State for explicit data flow and structured Pydantic outputs
for reliable LLM reasoning.

## Architecture

```
agent/core/             # Graph, state, model routing, LLM factory, events, middleware
  graph.py              # create_research_graph() — main graph construction
  state.py              # AgentState, SupervisorState, ResearcherState + Pydantic tools
  configuration.py      # ResearchConfiguration — 50+ runtime-configurable settings
  model_routing.py      # Configurable model with 3-tier + per-task-type routing
  llm_factory.py        # Centralized ChatOpenAI creation
  events.py             # SSE event emitter (TOOL_START, RESEARCH_TREE_UPDATE, etc.)
  middleware.py          # ToolErrorHandler, LoopDetector, TokenUsageTracker, MemoryMiddleware
  prompts.py            # resolve_prompt() template loading

agent/workflows/        # Deep research pipeline nodes
  input_gateway.py      # clarify → research_brief → classify_complexity
  research_plan.py      # HITL plan generation with LangGraph interrupt
  supervisor.py         # Supervisor subgraph (orchestrator loop + parallel spawn)
  researcher.py         # Researcher subgraph (ReAct loop + mixed compression)
  report.py             # Final report + HTML/Markdown + image injection
  quality_check.py      # Level 1 instant validation + auto-revise
  evaluation.py         # Level 2 (9-dim) and Level 3 (4-dim deep) evaluation
  multimodal.py         # Image injection into LLM context
  source_routing.py     # ResearchSourceRoutingPolicy (web/rag/hybrid/mcp modes)
  continuation.py       # Auto-continuation handler
  agent_factory.py      # build_writer_agent(), build_tool_agent()

agent/runtime/          # Lightweight runtime harness
  context.py            # RuntimeContext dataclass
  sandbox_policy.py     # Command auditing, pipe-to-shell detection
  user_context.py       # Per-request user_id via ContextVar
  middleware/shared.py  # Shared graph-middleware functions (loop check, token tracking, context budget)
  memory/               # Per-user memory (structured + embedding dual-mode)
    system.py           # MemorySystem: load/save/record/get_relevant_context
    storage.py          # File-based per-user JSON storage
    queue.py            # Debounced async update queue
    updater.py          # LLM-driven fact extraction

agent/skills/           # Skills system (SKILL.md parsing + tool allowlisting)
agent/mcp/              # MCP OAuth + interceptor
agent/prompts/          # Prompt templates (agent_prompts.py, system_prompts.py)
agent/tools/            # Agent-side tools (view_image, extract_web_images, skill_manage)
agent/api/              # API sub-routers (documents, tracing)

channels/               # IM channel integration (Feishu)
common/                 # Shared utilities (config, rate limiter, stream registry, tracing)
tools/                  # Tool implementations (search, sandbox, browser, code, crawl, rag, etc.)
```

## Cross-Cutting Concerns

### Middleware (4 core concerns in agent/core/middleware.py)

1. **ToolErrorHandling** — Tool failures return ToolMessages, never crash the graph
2. **LoopDetection** — Hash-based + frequency-based duplicate response detection
3. **TokenUsage** — Per-phase (supervisor/research/report) cost tracking
4. **Memory** — Async per-user memory update (fire-and-forget after report)

### Shared Graph Middleware (agent/runtime/middleware/shared.py)

- `check_loop()` — ReAct loop safety (used by supervisor and researcher nodes)
- `record_token_usage()` — Per-phase cost attribution
- `enforce_context_budget()` — Trim messages, cap tool results at 8K chars
- `build_dynamic_context_reminder()` — Date + memory as `<system-reminder>` block
- `safe_execute_tool()` — Wrap tool calls with error handling

### Context Budget Enforcement

Follows Claude Code's sub-agent principle: "the subagent does that work in its own
context and returns only the summary."

- Supervisor: max 40 messages, ConductResearch results capped at 8K chars
- ThinkTool reflections preferentially retained (high-signal structural info)
- Researcher results trimmed before entering supervisor context

## Key Design Decisions

- **Static system prompt**: Identical across users/sessions for maximum prefix-cache reuse.
  Dynamic content (date, memory) injected via `<system-reminder>` as hidden messages.
- **Structured outputs**: All LLM decisions (ConductResearch, ThinkTool, ComplexityAssessment,
  ResearchComplete) use Pydantic models bound as tools — no unstructured text parsing.
- **Subgraph nesting**: Supervisor and Researcher are independently compiled StateGraphs with
  clear input/output boundaries. Researcher subgraphs run in parallel via asyncio.gather.
- **ThinkTool convergence**: The Supervisor uses structured reflection (gaps/confidence/strategy)
  to autonomously decide when research is complete — no fixed iteration count.
- **Mixed compression**: Small content → pass-through, Medium → embedding similarity filter,
  Large → LLM semantic compression. Each level has a fallback.
- **Three-tier model routing**: fast_llm (mechanical), smart_llm (synthesis), strategic_llm
  (reasoning-heavy decisions). Plus 8 per-task-type overrides.
- **HITL plan gate**: Research plan generated before expensive supervisor execution, paused
  via LangGraph interrupt for user approve/revise/cancel.
- **Quality evaluation**: Three levels — Level 1 fast auto-check with auto-revise, Level 2
  9-dim weighted scoring, Level 3 4-dim deep eval with degradation detection.
- **Memory flow**: Report generation → MemoryMiddleware → MemorySystem.record_research()
  (sync file write) → next-turn injection via get_relevant_context().

## File Conventions

- `agent/workflows/` nodes import from `agent.core` (graph, state, configuration) — never the reverse
- `agent/runtime/` imports from `agent.workflows`, `agent.skills`, `common`, `tools` — never the reverse
- `agent/skills/` is independent of `agent/runtime/` (can be loaded standalone)
- `channels/` imports from `agent.runtime`, `common` — never the reverse
- `common/config.py` is the single source of truth for all Pydantic Settings fields
- New config fields should be added to `common/config.py` with sensible defaults

## Development Commands

```bash
# Start Weaver
./start_weaver.sh

# Run tests
PYTHONPATH=. python -m pytest tests/ -v

# Syntax check
python -c "import py_compile; ..."
```

## Git Workflow

- Check current branch and working tree before changing files
- Preserve unrelated local changes
- Run syntax checks before handoff
- Do NOT commit secrets (.env, credentials.json)
- Commit messages in English, present tense
