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
  llm_factory.py        # Centralized ChatOpenAI creation (multi-provider)
  events.py             # SSE event emitter (TOOL_START, RESEARCH_TREE_UPDATE, etc.)
  middleware.py          # ToolErrorHandler, LoopDetector, TokenUsageTracker
  prompts.py            # resolve_prompt() template loading
  message_utils.py      # Message manipulation utilities
  search_cache.py       # Search result caching with fuzzy query matching

agent/workflows/        # Deep research pipeline nodes
  input_gateway.py      # clarify → research_brief → classify_complexity
  research_plan.py      # HITL plan generation with LangGraph interrupt
  supervisor.py         # Supervisor subgraph (orchestrator loop + parallel spawn)
  researcher.py         # Researcher subgraph (ReAct loop + mixed compression)
  report.py             # Final report + HTML/Markdown + image injection
  quality_check.py      # Level 1 instant validation + auto-revise
  evaluation.py         # Level 2 (9-dim) and Level 3 (4-dim deep) evaluation
  multimodal.py         # Image injection into LLM context
  continuation.py       # Auto-continuation handler for multi-turn tool calls
  agent_factory.py      # build_writer_agent(), build_tool_agent() with middleware
  agent_tools.py        # Agent-specific tool definitions
  evidence_extractor.py # Extract evidence/sources from messages
  research_brief.py     # Research brief construction
  claim_verifier.py     # Claim verification with evidence alignment
  rubric.py             # Quality rubric definitions
  source_registry.py    # Source registry and scoring
  constants.py          # Shared constants
  agents/               # Multi-agent sub-module
    coordinator.py      # Task decomposition and agent orchestration
    planner.py          # Research strategy and sub-task planning
    researcher.py       # Deep-dive investigation agent
    reporter.py         # Synthesis and report generation agent

agent/retrieval/        # RetrievalPolicy v3 + unified retrieval gateway
  policy.py             # source_origin / access_channel / retrieval_method / profile
  gateway.py            # retrieve_sources/read_source tools
  documents.py          # user document library parsing, chunking, hybrid ranking
  types.py              # normalized retrieval result DTOs

agent/runtime/          # Lightweight runtime harness
  context.py            # RuntimeContext dataclass
  runs.py               # RunManager + persistent/fallback run records
  sandbox_policy.py     # Command auditing, pipe-to-shell detection
  user_context.py       # Per-request user_id via ContextVar
  middleware/shared.py  # Shared graph-middleware functions (loop check, token tracking, context budget)

agent/memory/           # Unified long-term memory service
  models.py             # MemoryRecord, MemoryEntity, MemoryRelation, MemoryEpisode
  store.py              # Postgres/pgvector store + in-memory test backend
  service.py            # retrieve(), ingest_research_run(), upsert_record(), delete_record()
  retrieval.py          # Hybrid vector/full-text/recency/importance/confidence/graph recall
  ingestion.py          # Evidence-gated research artifact memory extraction
  formatting.py         # Hidden <memory_context> formatter
  skill_evolution.py    # Procedural memory -> custom skill proposals

agent/skills/           # Skills system (SKILL.md parsing + tool allowlisting + security scanning)
agent/mcp/              # MCP OAuth + interceptor
agent/prompts/          # Prompt templates (agent_prompts.py, system_prompts.py, prompt_loader.py)
agent/parsers/          # XML tool call parser
agent/tools/            # Agent-side tools (view_image, extract_web_images, skill_manage)
agent/api/              # API sub-routers (deps, documents, models, tracing)

channels/               # IM channel integration (Feishu WebSocket + card streaming)
common/                 # Shared utilities (config, rate limiter, stream registry, tracing, session manager)
tools/                  # 16+ tool categories: search (multi-engine + academic + feeds), sandbox, browser,
                        #   code, crawl, rag, planning, export, automation, research, io
prompts/                # Prompt template packs (deepresearch, deepsearch, optimizer)
skills/                 # 28 built-in SKILL.md skill definitions (public/ + custom/)
scripts/                # CLI tools, benchmarks (GAIA, deep research), smoke tests
```

## Cross-Cutting Concerns

### Middleware (3 core concerns in agent/core/middleware.py)

1. **ToolErrorHandling** — Tool failures return ToolMessages, never crash the graph
2. **LoopDetection** — Hash-based + frequency-based duplicate response detection
3. **TokenUsage** — Per-phase (supervisor/research/report) cost tracking

### Shared Graph Middleware (agent/runtime/middleware/shared.py)

- `check_loop()` — ReAct loop safety (used by supervisor and researcher nodes)
- `record_token_usage()` — Per-phase cost attribution
- `enforce_context_budget()` — Trim messages, cap tool results at 8K chars
- `safe_execute_tool()` — Wrap tool calls with error handling

### Quality Gates (evaluation pipeline)

- **Citation Gate**: Enforces minimum citation coverage (configurable, default 60%)
- **Claim Verifier Gate**: Maximum allowed contradicted/unsupported claims (default 0)
- **Rubric Scoring**: Fine-grained rubric-based evaluation with calibration tracking
- **Auto-Revise Loop**: Level 1 fail triggers up to 2 automatic revisions before report delivery

### Tool Reliability

- **Tool Retry**: Exponential backoff retry for transient tool failures (3 attempts default)
- **Tool Selector**: Provider-safe tool filtering reduces token overhead by limiting tool visibility
- **Dynamic Tool Pruning**: Route-based tool filtering (optional, off by default)
- **Context Offloading**: Large tool results offloaded to filesystem to save context window

### Context Management

- **Summarization**: Automatic conversation summarization when message count exceeds trigger threshold
- **Observation Masking**: Old tool observations partially masked (keep last N turns in full)
- **Context Editing**: Trim or compress tool results when token budget exceeded
- **Message Trimming**: Configurable keep-first/keep-last window on message history

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
  (reasoning-heavy decisions). Plus 8 per-task-type overrides. Provider-agnostic.
- **HITL plan gate**: Research plan generated before expensive supervisor execution, paused
  via LangGraph interrupt for user approve/revise/cancel.
- **Quality evaluation**: Three levels — Level 1 fast auto-check with auto-revise, Level 2
  9-dim weighted scoring, Level 3 4-dim deep eval with degradation detection. Plus citation
  gate and claim verifier gate.
- **Memory flow**: `report.py` ingests completed research artifacts through
  `MemoryService.ingest_research_run()`. New requests call `MemoryService.retrieve()`
  before graph execution and inject hidden `<memory_context>` as research leads only;
  final citations still require current-run evidence provenance.
- **Multi-agent mode**: Optional Coordinator/Planner/Researcher/Reporter agents with
  structured inter-agent messaging for complex multi-perspective research.
- **Skill safety**: Skills declare allowed tools; installed skills undergo security scanning;
  skill evolution is configurable (off by default).

## File Conventions

- `agent/workflows/` nodes import from `agent.core` (graph, state, configuration) — never the reverse
- `agent/runtime/` imports from `agent.workflows`, `agent.skills`, `common`, `tools` — never the reverse
- `agent/skills/` is independent of `agent/runtime/` (can be loaded standalone)
- `agent/prompts/` is independent of `agent/workflows/` and `agent/runtime/`
- `channels/` imports from `agent.runtime`, `common` — never the reverse
- `common/config.py` is the single source of truth for all Pydantic Settings fields (~250+ fields)
- `tools/` modules are self-contained tool implementations, imported by `agent.workflows.agent_factory`
- `prompts/templates/` contains YAML prompt packs loaded by `agent.core.prompts`
- New config fields should be added to `common/config.py` with sensible defaults

## Dependency Layers

```
prompts/            (standalone templates, no code imports)
skills/             (standalone SKILL.md files, no code imports)
tools/              (imports common, agent.core)
agent/skills/       (imports common)
agent/prompts/      (imports common)
agent/mcp/          (imports common)
agent/parsers/      (standalone)
agent/tools/        (imports common, tools)
agent/workflows/    (imports agent.core, agent.runtime, tools, common)
agent/runtime/      (imports agent.workflows, agent.skills, common, tools)
agent/api/          (imports agent.workflows, agent.runtime, common)
channels/           (imports agent.runtime, common)
common/config.py    (zero internal imports — foundation layer)
```

## Development Commands

```bash
# Start Weaver
./start_weaver.sh

# Backend only
python main.py
# or with hot reload
DEBUG=true WEAVER_RELOAD=1 python main.py

# Run tests
PYTHONPATH=. python -m pytest tests/ -v

# Lint
make lint          # Changed files only
make lint-all      # Full repo

# Full verification
make verify        # compile check + tests + OpenAPI types + smoke test

# Docker
docker compose -f docker/docker-compose.yml up
```

## Git Workflow

- Check current branch and working tree before changing files
- Preserve unrelated local changes
- Run syntax checks before handoff: `python -c "import py_compile; ..."`
- Do NOT commit secrets (.env, credentials.json, real API keys)
- Commit messages in English, present tense
- Main branch: `main`; active development on feature branches
