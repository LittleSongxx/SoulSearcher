<div align="center">

# Weaver — AI Deep Research Agent Platform

**LangGraph · Supervisor-Workers · Three-Tier Model Routing · Skills & MCP · Persistent Memory · Sandbox Isolation · Multi-Agent**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.134+-009688?style=flat&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-7B68EE?style=flat&logo=databricks&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-black?style=flat&logo=next.js&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)

</div>

---

## Architecture

Weaver is a **Deep Research Agent** platform built on LangGraph. It decomposes complex research tasks into a multi-stage graph pipeline: clarify → plan → supervisor → N parallel researchers → report. Every stage uses typed State for explicit data flow and structured Pydantic outputs for reliable LLM reasoning.

```
                          User Query
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                     INPUT GATEWAY                             │
│                                                               │
│  clarify_with_user ─→ write_research_brief ─→ classify       │
│       │                      │                    │           │
│   [ambiguous → END]    [always next]     simple → direct      │
│                                            deep → plan        │
└──────────────────────────────────────────────────────────────┘
                              │ deep
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                     PLAN GATE (HITL)                          │
│                                                               │
│  plan_research → LangGraph interrupt → user approve/reject   │
│       │                  │                                    │
│   [cancel → END]    [approve → execute]                       │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  SUPERVISOR SUBGRAPH                          │
│                                                               │
│  supervisor ⇄ supervisor_tools                               │
│    tools: ConductResearch  — spawn parallel researchers       │
│           ThinkTool        — structured reflection            │
│           SourceCurate     — rank & filter sources            │
│           ResearchComplete — signal completion                │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  RESEARCHER SUBGRAPHS (N parallel, asyncio.gather)  │     │
│  │  researcher ⇄ researcher_tools → compress_research  │     │
│  │    tools: search (Tavily/ArXiv/PubMed/Semantic),    │     │
│  │           sandbox (shell/files/code/vision/browser),│     │
│  │           feeds (Twitter/Reddit/HackerNews),        │     │
│  │           MCP, RAG, crawl, code, ThinkTool          │     │
│  │    compression: raw ↛ embedding filter ↛ LLM refine │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                   REPORT & EVALUATION                         │
│                                                               │
│  final_report → Level 1 auto-check → auto-revise (×2)       │
│               → Level 2 evaluation (9-dim weighted)          │
│               → Level 3 deep eval (4-dim + degradation)      │
│               → Citation gate → Claim verifier gate          │
│               → Memory update (structured + embedding)       │
│               → HTML / Markdown / PDF output                 │
└──────────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Explicit Data Flow**: Every graph node defines typed `Command[goto, update]` — no hidden state mutations
- **Structured Outputs**: All LLM decisions use Pydantic models (ConductResearch, ThinkTool, ComplexityAssessment, etc.)
- **Three-Tier Model Routing**: fast_llm for high-volume tasks, smart_llm for synthesis, strategic_llm for planning — plus per-task-type overrides
- **Subgraph Nesting**: Supervisor and Researcher are independently compiled StateGraphs with clear input/output boundaries
- **Mixed Compression**: Small → pass-through, Medium → embedding similarity filter, Large → LLM semantic compression
- **Context Budget Enforcement**: "Sub-agents work in their own context, return only the summary"
- **Quality Gates**: Multi-level evaluation with citation coverage, claim verification, and automatic revision loops

### Key Capabilities

| System | Description |
|--------|-------------|
| **Deep Research Graph** | Multi-stage pipeline: clarify → plan → supervisor → N parallel researchers → report |
| **Adaptive Routing** | Complexity classification routes simple queries to fast direct-answer, complex to full pipeline |
| **Three-Tier Models** | fast_llm / smart_llm / strategic_llm + 8 task-type overrides for fine-grained cost optimization |
| **HITL Plan Gate** | Research plan generated before execution, paused via LangGraph interrupt for user approve/revise/cancel |
| **Multi-Agent** | Coordinator / Planner / Researcher / Reporter agents with structured inter-agent communication |
| **Quality Evaluation** | Three levels + citation gate + claim verifier gate + rubric scoring + calibration tracking |
| **Structured Reflection** | ThinkTool captures gaps/confidence/strategy, enabling high-confidence auto-completion |
| **Skills** | 28 built-in SKILL.md skills (academic review, data analysis, chart viz, podcast, etc.), public/custom categories, tool allowlisting |
| **Memory** | Per-user file-based storage + embedding semantic index, LLM-driven fact extraction, debounced async updates |
| **MCP** | OAuth support (client_credentials/refresh_token), deferred tool search, mtime cache invalidation |
| **Multimodal** | Vision support (view_image, extract_web_images), user-uploaded image injection, HTML report image embedding |
| **Sandbox** | E2B/Daytona isolation (shell, files, browser, vision, sheets, presentations), opt-in host bash with command auditing |
| **Search** | Multi-engine (Tavily, Bocha, DuckDuckGo, Serper, Brave, Bing, Exa, Google CSE) with fallback, circuit breaker, freshness ranking |
| **Academic Search** | arXiv, PubMed/NCBI, Semantic Scholar with rate limiting and API key rotation |
| **Real-time Feeds** | Twitter/X API v2, Reddit, HackerNews for time-sensitive research |
| **Middleware** | ToolErrorHandler, LoopDetector, TokenUsageTracker, MemoryMiddleware, TodoMiddleware, ToolSelector |
| **Agent Reflexion** | Self-reflection after tool-calling rounds for error recovery and strategy adjustment |
| **Channels** | Feishu/Lark IM integration via abstract Channel base class with card streaming |
| **Tool Registry** | Dynamic registration/discovery, tag-based lookup, usage statistics, LangChain compatibility |
| **Export** | Markdown / HTML / PDF (WeasyPrint) with chart visualization and image embedding |
| **SDK** | Python + TypeScript internal SDKs for programmatic API access |

---

## Project Structure

```
Weaver/
├── agent/
│   ├── core/                   # Graph, state, model routing, LLM factory, events
│   │   ├── graph.py            # create_research_graph() — main entry point
│   │   ├── state.py            # AgentState, SupervisorState, ResearcherState + Pydantic tools
│   │   ├── configuration.py    # ResearchConfiguration — 50+ runtime-configurable settings
│   │   ├── model_routing.py    # Configurable model with 3-tier + per-task-type routing
│   │   ├── llm_factory.py      # Centralized ChatOpenAI creation with multi-provider support
│   │   ├── events.py           # SSE event emitter (TOOL_START, RESEARCH_TREE_UPDATE, etc.)
│   │   ├── middleware.py        # ToolErrorHandler, LoopDetector, TokenUsageTracker, MemoryMiddleware
│   │   ├── prompts.py          # resolve_prompt() — template-based prompt loading
│   │   ├── message_utils.py    # Message manipulation utilities
│   │   ├── search_cache.py     # Search result caching with fuzzy query matching
│   │   └── processor_config.py # Processor configuration
│   ├── workflows/              # Deep research pipeline nodes
│   │   ├── input_gateway.py    # clarify_with_user → write_research_brief → classify_complexity
│   │   ├── research_plan.py    # HITL plan generation with LangGraph interrupt
│   │   ├── supervisor.py       # Supervisor subgraph (orchestrator loop + parallel spawn)
│   │   ├── researcher.py       # Researcher subgraph (ReAct loop + mixed compression)
│   │   ├── report.py           # Final report + HTML/Markdown + image injection + quality check
│   │   ├── quality_check.py    # Level 1 instant quality validation + auto-revise
│   │   ├── evaluation.py       # Level 2 (9-dim) and Level 3 (4-dim deep) evaluation
│   │   ├── multimodal.py       # Image injection into LLM context
│   │   ├── source_routing.py   # ResearchSourceRoutingPolicy (web/rag/hybrid/mcp modes)
│   │   ├── continuation.py     # Auto-continuation handler for multi-turn tool calling
│   │   ├── agent_factory.py    # build_writer_agent(), build_tool_agent() with middleware
│   │   ├── agent_tools.py      # Agent-specific tool definitions
│   │   ├── evidence_extractor.py  # Extract evidence/sources from messages
│   │   ├── research_brief.py   # Research brief construction
│   │   ├── claim_verifier.py   # Claim verification with evidence alignment
│   │   ├── rubric.py           # Quality rubric definitions
│   │   ├── source_registry.py  # Source registry and scoring
│   │   ├── source_url_utils.py # Source URL normalization utilities
│   │   ├── structural_text.py  # Structural text processing
│   │   ├── constants.py        # Shared constants
│   │   ├── gaia_mode.py        # GAIA benchmark evaluation mode
│   │   └── agents/             # Multi-agent sub-module
│   │       ├── coordinator.py  # Agent coordinator
│   │       ├── planner.py      # Planning agent
│   │       ├── reporter.py     # Reporting agent
│   │       └── researcher.py   # Research agent
│   ├── runtime/                # Lightweight runtime harness
│   │   ├── context.py          # RuntimeContext dataclass (thread_id, user_id, model, channel)
│   │   ├── sandbox_policy.py   # Command auditing, pipe-to-shell detection, audit logging
│   │   ├── user_context.py     # Per-request user_id resolution via ContextVar
│   │   ├── middleware/shared.py # Shared graph-middleware: loop check, token tracking, context budget
│   │   └── memory/             # Per-user memory (structured + embedding dual-mode)
│   │       ├── system.py       # MemorySystem: load/save/record/get_relevant_context
│   │       ├── storage.py      # File-based per-user JSON storage
│   │       ├── queue.py        # Debounced async update queue
│   │       ├── updater.py      # LLM-driven fact extraction
│   │       └── prompt.py       # Memory system prompt builder
│   ├── skills/                 # Skills system
│   │   ├── types.py            # Skill dataclass, SkillCategory enum (PUBLIC/CUSTOM)
│   │   ├── parser.py           # SKILL.md YAML frontmatter parser
│   │   ├── prompt.py           # Skill system-prompt section builder with progressive loading
│   │   ├── tool_policy.py      # Skill-based tool allowlist enforcement
│   │   ├── validation.py       # Skill content validation
│   │   ├── installer.py        # Skill installation from remote sources
│   │   ├── security_scanner.py # Skill security scanning
│   │   └── storage/            # Skill storage backends
│   ├── mcp/                    # MCP integration
│   │   └── oauth.py            # MCP OAuth token manager + interceptor
│   ├── prompts/                # Prompt templates
│   │   ├── agent_prompts.py    # Agent prompt definitions
│   │   ├── system_prompts.py   # System prompt definitions
│   │   ├── prompt_loader.py    # Prompt loading utilities
│   │   └── prompt_manager.py   # Prompt manager
│   ├── parsers/                # XML tool call parser
│   │   └── xml_parser.py
│   ├── tools/                  # Agent-side tools
│   │   ├── view_image.py       # Vision/image viewing tool
│   │   ├── extract_web_images.py  # Web image extraction
│   │   └── skill_manage.py     # Skill management tool
│   └── api/                    # API sub-routers
│       ├── deps.py             # API dependencies
│       ├── documents.py        # RAG document management endpoints
│       ├── models.py           # API Pydantic models
│       └── tracing.py          # Tracing/debug endpoints
├── common/                     # Shared utilities
│   ├── config.py               # Pydantic Settings — single source of truth for all config (~1064 lines)
│   ├── logger.py               # Logging setup (file rotation, JSON formatting)
│   ├── rate_limiter.py         # Token-bucket rate limiter
│   ├── stream_registry.py      # Active SSE stream tracker
│   ├── tracing.py              # OpenTelemetry request tracing
│   ├── cancellation.py         # Cooperative task cancellation
│   ├── session_manager.py      # Session lifecycle management
│   ├── agents_store.py         # Agent profile storage
│   ├── evidence_store.py       # Evidence storage and snapshotting
│   ├── collaboration.py        # Collaboration support
│   ├── concurrency.py          # Concurrency utilities
│   ├── e2b_env.py              # E2B sandbox environment
│   ├── extensions_config.py    # Extensions configuration loader
│   ├── metrics.py              # Prometheus metrics registry
│   ├── proxy_env.py            # SOCKS proxy normalization
│   ├── research_events.py      # Research event building
│   ├── sse.py                  # SSE formatting utilities
│   ├── thread_ownership.py     # Thread-to-owner mapping
│   └── uvicorn_reload.py       # Hot-reload customization
├── tools/                      # Tool implementations (16+ categories)
│   ├── core/                   # Tool registry, base types, wrappers, MCP clients, LangChain adapter
│   ├── search/                 # Tavily, Bocha, DuckDuckGo, Serper, Brave, Bing, Exa, Google CSE
│   │   ├── academic/           # arXiv, PubMed, Semantic Scholar
│   │   └── feeds/              # Twitter/X, Reddit, HackerNews
│   ├── sandbox/                # E2B/Daytona: shell, files, browser, vision, sheets, presentations, web dev
│   ├── browser/                # CDP-based browser automation (browser_use, content extraction)
│   ├── code/                   # Python code executor (local + enhanced), chart visualization
│   ├── crawl/                  # Web crawling (Crawl4AI, Playwright crawler)
│   ├── rag/                    # Document loader, embedder, vector store (ChromaDB)
│   ├── planning/               # Planning tool
│   ├── export/                 # Markdown converter
│   ├── automation/             # Bash, string replace, task list
│   ├── research/               # Content fetcher, Jina Reader client, page cache
│   └── io/                     # Screenshot service
├── prompts/                    # Prompt templates
│   ├── planning.py
│   └── templates/
│       ├── deepresearch/       # Deep research prompts
│       ├── deepsearch/         # Deep search prompts (behavior, domains, gap_analysis, etc.)
│       └── optimizer/          # Prompt optimizer (analyzer, evaluator, optimizer)
├── skills/                     # Skill definitions
│   ├── public/                 # 28 built-in skills (academic review, data analysis, chart viz, podcast, etc.)
│   └── custom/                 # User-authored skills directory
├── channels/                   # IM channel integration
│   ├── base.py                 # Abstract Channel ABC
│   ├── feishu.py               # Feishu/Lark WebSocket + card streaming
│   ├── manager.py              # ChannelManager dispatcher
│   ├── message_bus.py          # Async pub/sub message bus
│   ├── service.py              # Channel lifecycle service
│   └── store.py                # Channel store
├── web/                        # Next.js 14 frontend with SSE streaming, research tree viz
├── sdk/                        # Python + TypeScript internal SDKs
├── scripts/                    # CLI tools, benchmarks, smoke tests, secret scan
├── docker/                     # Docker Compose (postgres, redis, backend, frontend)
├── config/                     # TOML/YAML configuration examples
├── data/                       # Runtime data (agents, collaboration)
├── main.py                     # FastAPI application (50+ endpoints)
├── Makefile                    # Development targets (setup, dev, test, lint, verify, etc.)
├── start_weaver.sh             # Startup script (auto-generates .env/config on first run)
└── stop_weaver.sh              # Shutdown script
```

---

## Quick Start

```bash
git clone https://github.com/LittleSongxx/Weaver_pro.git
cd Weaver_pro

# Start script auto-generates .env and config on first run
./start_weaver.sh
```

Minimum `.env` configuration:
```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com    # or any OpenAI-compatible provider
TAVILY_API_KEY=tvly-...                     # web search API key
```

Access:
- **Web UI**: `http://127.0.0.1:3100`
- **API**: `http://127.0.0.1:8001`
- **API Docs**: `http://127.0.0.1:8001/docs`

---

## Deep Research Pipeline

### Flow

1. **Clarify** (`clarify_with_user`) — Structured clarification analysis. If the user query is ambiguous or missing key details, returns a clarifying question and ends. Otherwise proceeds.

2. **Research Brief** (`write_research_brief`) — Transforms user messages into a structured research brief. Active Skills' methodology is injected as guidance context.

3. **Complexity Classification** (`classify_complexity`) — Routes to fast direct-answer (simple factual queries) or full Orchestrator-Workers pipeline (deep analytical queries).

4. **Research Plan** (`plan_research`) — [HITL] Generates a structured plan (sub-topics, search strategy, source preferences, expected output) and pauses via LangGraph interrupt for user approval.

5. **Supervisor Loop** (`supervisor ⇄ supervisor_tools`) — The supervisor LLM calls structured tools to manage the research:
   - `ConductResearch` — spawns N parallel Researcher subgraphs via `asyncio.gather`
   - `ThinkTool` — structured reflection (gaps, confidence, next strategy)
   - `SourceCurate` — ranks collected sources by quality and relevance
   - `ResearchComplete` — ends the loop when confidence is high

6. **Researcher Subgraphs** (N parallel instances) — Each researcher executes an independent ReAct loop with search (multi-engine + academic + feeds), sandbox, browser, RAG, crawl, and MCP tools, then compresses findings via mixed strategy.

7. **Report & Evaluation** — Synthesizes all findings into structured report with:
   - Automatic image embedding and chart generation
   - Level 1 fast quality auto-check with up to 2 auto-revisions
   - Level 2 weighted multi-dimension evaluation
   - Citation coverage gate and claim verification gate
   - HTML / Markdown / PDF output formats

### Model Routing

The platform uses a three-tier model strategy inspired by Anthropic's "Building Effective Agents":

| Tier | Default Model | Use Case |
|------|--------------|----------|
| `fast_llm` | deepseek-v4-flash / qwen3.6-flash | Summarization, quality checks, simple classification |
| `smart_llm` | deepseek-v4-flash / qwen3.6-plus | Research synthesis, report writing, compression |
| `strategic_llm` | deepseek-v4-pro / qwen3.7-max | Planning, strategy decisions, deep analysis, evaluation |

All model names are configurable via `FAST_LLM_MODEL` / `SMART_LLM_MODEL` / `STRATEGIC_LLM_MODEL` in `.env`. Provider-agnostic — works with DeepSeek, OpenAI, Anthropic, DashScope (Qwen), or any OpenAI-compatible API.

Plus **8 task-type specific overrides**: planner, researcher, writer, evaluator, critic, compression, summarization, final_report.

### Compression Strategy

| Content Size | Strategy | Technique |
|-------------|----------|-----------|
| < 8K chars | Raw pass-through | Zero-cost |
| 8K–50K chars | Embedding similarity filter | text-embedding-3-small → relevance threshold |
| > 50K chars | LLM semantic compression | smart_llm rewrites findings into concise summary |

Each level has a fallback. Context budget enforcement caps supervisor messages at 40 and tool results at 8K chars.

### Quality Evaluation

| Level | Model | Dimensions | Action |
|-------|-------|-----------|--------|
| Level 1 | fast_llm | 6-dim (citation density, section completeness, format, relevance, length, evidence alignment) | Auto-revise up to 2× |
| Level 2 | smart_llm | 9-dim weighted scoring (completeness, accuracy, coherence, citation, structure, depth, clarity, objectivity, insight) | Post-report evaluation |
| Level 3 | strategic_llm | 4-dim (coverage, accuracy, freshness, coherence) + degradation detection | Deep quality monitoring |

Additional quality gates:
- **Citation Gate**: Minimum citation coverage threshold (configurable, default 60%)
- **Claim Verifier Gate**: Maximum allowed contradicted/unsupported claims (default 0)
- **Rubric Scoring**: Fine-grained rubric-based evaluation with calibration tracking

### Structured Reflection (ThinkTool)

The ThinkTool enables the Supervisor to perform structured self-reflection with actionable outputs:

```
reflection: What we've learned, patterns, contradictions
gaps_identified: [specific missing data points]
confidence_level: low | medium | high
next_strategy: search_more | curate | complete
```

This is the key mechanism for **convergence control** — the Supervisor autonomously decides when research is complete.

---

## Multi-Agent System

Weaver includes an optional multi-agent mode (`agent/workflows/agents/`) with four specialized agents:

| Agent | Role |
|-------|------|
| **Coordinator** | Task decomposition and agent orchestration |
| **Planner** | Research strategy and sub-task planning |
| **Researcher** | Deep-dive investigation with tool access |
| **Reporter** | Synthesis and final report generation |

Agents communicate via structured inter-agent messages, enabling complex multi-perspective research workflows.

---

## API Endpoints

### Research

| Endpoint | Description |
|----------|-------------|
| `POST /api/research/sse` | Main research SSE streaming endpoint |
| `POST /api/research/cancel/{thread_id}` | Cancel an active research run |
| `POST /api/research/cancel-all` | Cancel all active research runs |
| `POST /api/research/fork` | Fork a research session |

### Interrupt (HITL)

| Endpoint | Description |
|----------|-------------|
| `GET /api/interrupt/{thread_id}/status` | Get interrupt status for a thread |
| `POST /api/interrupt/{thread_id}/resume` | Resume after HITL interrupt |

### Sessions

| Endpoint | Description |
|----------|-------------|
| `GET /api/sessions` | List all sessions |
| `GET /api/sessions/{thread_id}` | Get session details |
| `GET /api/sessions/{thread_id}/state` | Get raw LangGraph state |
| `GET /api/sessions/{thread_id}/evidence` | Get extracted evidence/sources |
| `POST /api/sessions/{thread_id}/resume` | Resume a paused session |
| `DELETE /api/sessions/{thread_id}` | Delete a session |

### Skills

| Endpoint | Description |
|----------|-------------|
| `GET /api/skills` | List all skills (`?enabled_only=true`) |
| `GET /api/skills/{name}` | Get skill details |
| `PUT /api/skills/{name}` | Enable/disable a skill |
| `POST /api/skills/install` | Install a skill from remote |
| `GET /api/skills/custom` | List custom skills |
| `PUT /api/skills/custom/{name}` | Update custom skill |
| `DELETE /api/skills/custom/{name}` | Delete custom skill |

### Tools & Search

| Endpoint | Description |
|----------|-------------|
| `GET /api/tools/registry` | List registered tools with metadata |
| `POST /api/tools/registry/refresh` | Refresh tool registry |
| `GET /api/search/providers` | List search providers |
| `GET /api/search/cache/stats` | Search cache statistics |

### Channels, Memory, Export

| Endpoint | Description |
|----------|-------------|
| `GET /api/channels` | IM channel status |
| `POST /api/channels/{name}/restart` | Restart a channel |
| `GET /api/memory/status` | Memory system status |
| `GET /api/export/{thread_id}` | Export research as artifact |
| `GET /api/export/templates` | List export templates |

### Documents (RAG)

| Endpoint | Description |
|----------|-------------|
| `POST /api/documents/upload` | Upload documents for RAG indexing |
| `GET /api/documents` | List indexed documents |
| `DELETE /api/documents/{doc_id}` | Remove a document |

---

## Configuration

Key settings in `.env` (see `.env.example` for full 360+ line annotated template):

```bash
# Core — LLM Provider
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com          # or https://dashscope.aliyuncs.com/compatible-mode/v1
PRIMARY_MODEL=deepseek-v4-flash
REASONING_MODEL=deepseek-v4-pro
DASHSCOPE_API_KEY=sk-...                          # Required for DashScope/Qwen models

# Three-Tier Model Routing
FAST_LLM_MODEL=deepseek-v4-flash                  # High-volume mechanical tasks
SMART_LLM_MODEL=deepseek-v4-flash                 # Synthesis and writing
STRATEGIC_LLM_MODEL=deepseek-v4-pro               # Planning and deep reasoning

# Per-phase model overrides (empty = use tier default)
PLANNER_MODEL=
RESEARCHER_MODEL=
WRITER_MODEL=
EVALUATOR_MODEL=
COMPRESSION_MODEL=
SUMMARIZATION_MODEL=
FINAL_REPORT_MODEL=

# Search
TAVILY_API_KEY=tvly-...
SEARCH_ENGINES=tavily,bocha                       # Multi-engine priority order
SEARCH_STRATEGY=fallback                          # fallback | parallel | round_robin | best_first

# Deepsearch
DEEPSEARCH_MODE=supervisor_workers
DEEPSEARCH_MAX_EPOCHS=3
DEEPSEARCH_SUPERVISOR_ROUNDS=2
DEEPSEARCH_SUPERVISOR_MAX_WORKERS=4
DEEPSEARCH_SUPERVISOR_PARALLEL_WORKERS=2

# Research limits
MAX_CONCURRENCY=5
MAX_REACT_TOOL_CALLS=8
TOOL_CALL_LIMIT=12

# Quality gates
CITATION_GATE_MIN_COVERAGE=0.6
CLAIM_VERIFIER_GATE_MAX_CONTRADICTED=0
CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED=0
EVALUATION_PASS_THRESHOLD=0.75
MAX_REVISIONS=2

# Report
REPORT_FORMAT=markdown                            # markdown | html
HTML_REPORT_EMBED_IMAGES=false
ENABLE_REPORT_CHARTS=true

# Vision / Multimodal
SUPPORTS_VISION=false
VISION_ENRICH_DATA=false

# Memory
MEMORY_ENABLED=true
MEMORY_INJECTION_ENABLED=true
MEMORY_STORAGE_PATH=data/memory
MEMORY_DEBOUNCE_SECONDS=30
MEMORY_MAX_FACTS=100

# MCP
ENABLE_MCP=false
MCP_STRATEGY=on_demand                            # disabled | fast_once | per_research_unit | on_demand

# Sandbox
SANDBOX_MODE=e2b                                  # e2b | daytona | none
HOST_BASH_ENABLED=false

# Channels
CHANNELS_ENABLED=false
FEISHU_CHANNEL_ENABLED=false

# Skills
SKILLS_PUBLIC_DIR=skills/public
SKILLS_CUSTOM_DIR=skills/custom
SKILL_EVOLUTION_ENABLED=false

# Security
WEAVER_INTERNAL_API_KEY=                          # Set to enable internal auth
TOOL_APPROVAL=false                               # Enable HITL for risky tools

# Middleware
TOOL_RETRY=true
LOOP_DETECTION_ENABLED=true
SUMMARIZATION_ENABLED=true
TOOL_SELECTOR=true
ENABLE_TODO_MIDDLEWARE=true
AGENT_REFLEXION_ENABLED=true

# RAG
RAG_ENABLED=false
RAG_EMBEDDING_MODEL=text-embedding-3-small

# Observability
ENABLE_PROMETHEUS=false
ENABLE_TRACING=false
LOG_LEVEL=INFO
```

---

## Skills System

Skills define reusable research methodologies via `SKILL.md` files with YAML frontmatter:

```markdown
---
name: systematic-literature-review
description: PRISMA-compliant systematic literature review methodology
license: MIT
allowed-tools:
  - arxiv_search
  - pubmed_search
  - semantic_scholar_search
---

# Systematic Literature Review

...
```

**28 built-in skills** cover: academic paper review, bootstrap, chart visualization, code documentation, consulting analysis, data analysis, deep research, find skills, frontend design, GitHub deep research, HTML report, image generation, newsletter generation, paper decomposition, podcast generation, PPT generation, reproducibility audit, research proposal generation, research trend analysis, science communication, skill creator, surprise me, systematic literature review, Vercel deploy, video generation, vision enrich, and web design guidelines.

Skills support:
- **Progressive Loading**: Agent sees skill list in prompt, loads full content on demand
- **Tool Allowlisting**: Each skill declares which tools the agent may use
- **Security Scanning**: Automatic security review of installed skills
- **Skill Evolution**: Agents can create/modify skills via `skill_manage` tool (configurable)

---

## Search System

### Multi-Engine Search

Weaver supports 9+ search providers with configurable strategies:

| Provider | Type | API Key Required |
|----------|------|-----------------|
| Tavily | AI-optimized web search | Yes |
| Bocha | Chinese-language search | Yes |
| DuckDuckGo | Privacy-focused web search | No |
| Serper | Google Search API | Yes |
| Brave | Privacy-focused web search | Yes |
| Bing | Microsoft Bing API | Yes |
| Exa | Neural semantic search | Yes |
| Google CSE | Custom Search Engine | Yes |
| Firecrawl | Web scraping with AI | Yes |

Search strategies: `fallback` (sequential), `parallel` (concurrent), `round_robin`, `best_first`.

### Reliability Features
- **Circuit Breaker**: Auto-disable failing providers after N consecutive failures
- **Retry with Backoff**: Exponential backoff for transient errors
- **Freshness Ranking**: Time-decay weighting for recency-sensitive queries
- **Query Cache**: Fuzzy-matched session-level cache with configurable TTL
- **API Key Pool**: Multi-key rotation for Tavily to avoid quota exhaustion

### Academic & Real-time Sources
- **Academic**: arXiv, PubMed/NCBI Entrez, Semantic Scholar
- **Feeds**: Twitter/X API v2, Reddit, HackerNews

---

## Sandbox System

Weaver supports isolated code execution via E2B or Daytona:

| Tool | Description |
|------|-------------|
| Shell | Execute Python/bash in isolated sandbox |
| Files | Read/write files in sandbox filesystem |
| Browser | Headed browser automation in sandbox |
| Vision | Screenshot capture and analysis |
| Sheets | Spreadsheet creation and manipulation |
| Presentations | Slide deck generation |
| Web Dev | Web development with live preview |
| Image Edit | Image manipulation and editing |

Optional host bash access with command auditing and pipe-to-shell detection for security.

---

## Middleware System

### Core Middleware (agent/core/middleware.py)

| Middleware | Function |
|-----------|----------|
| ToolErrorHandler | Catches tool failures, returns ToolMessages never crashes |
| LoopDetector | Hash-based + frequency-based duplicate response detection |
| TokenUsageTracker | Per-phase (supervisor/research/report) cost tracking |
| MemoryMiddleware | Async per-user memory update after report generation |

### Graph Middleware (agent/runtime/middleware/shared.py)

| Function | Purpose |
|----------|---------|
| `check_loop()` | ReAct loop safety across supervisor and researcher nodes |
| `record_token_usage()` | Per-phase cost attribution |
| `enforce_context_budget()` | Message trimming and tool result capping |
| `build_dynamic_context_reminder()` | Date + memory injection as `<system-reminder>` block |
| `safe_execute_tool()` | Wrapped tool execution with error handling |

### Additional Middleware

| Middleware | Function |
|-----------|----------|
| TodoMiddleware | Structured task planning and progress tracking |
| ToolSelector | Provider-safe tool filtering to reduce token overhead |
| Summarization | Automatic conversation summarization when context grows large |
| Agent Reflexion | Self-reflection after tool-calling rounds for error recovery |

---

## Development

```bash
# Setup
make setup          # Create venv, install core + dev deps
make setup-full     # Also install optional tools (crawl4ai, browser-use)

# Development
make dev            # Start backend (python main.py)
make dev-reload     # Start backend with hot reload

# Testing & Quality
make test           # Run pytest
make lint           # Lint changed files (ruff)
make lint-all       # Lint entire repo
make format         # Auto-format code
make secret-scan    # Scan for leaked API keys
make check          # lint + test + secret-scan
make verify         # Full verification (compile + test + types + smoke)

# Frontend
make web-install    # Install frontend deps (pnpm)
make web-lint       # Lint frontend
make web-build      # Build frontend

# Docker
docker compose -f docker/docker-compose.yml up   # Full stack (postgres, redis, backend, frontend)
```

---

## License

MIT License. See `LICENSE`.
