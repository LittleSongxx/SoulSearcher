<div align="center">

# Weaver — AI Deep Research Agent Platform

**LangGraph · Supervisor-Workers · Three-Tier Model Routing · Skills & MCP · Persistent Memory · Sandbox Isolation**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-7B68EE?style=flat&logo=databricks&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)

</div>

---

## Architecture

Weaver is a **Deep Research Agent** platform built on LangGraph. It decomposes complex research tasks into a multi-stage pipeline: clarify → plan → supervise → research (N parallel workers) → compress → report. Every stage uses typed State for explicit data flow and structured Pydantic outputs for reliable LLM reasoning.

```
                          User Query
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                     INPUT GATEWAY                             │
│                                                               │
│  clarify_with_user ─→ write_research_brief ─→ classify       │
│       │                      │                    │           │
│   [need? → END]         [always next]     simple → direct    │
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
│  │    tools: search (Tavily/ArXiv/PubMed), think_tool, │     │
│  │           sandbox (shell/files/code), MCP, vision   │     │
│  │    compression: raw ↛ embedding filter ↛ LLM refine │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                   REPORT GENERATION                           │
│                                                               │
│  final_report → Level 1 quality check → auto-revise (×2)     │
│               → Level 2 evaluation (9-dim weighted)           │
│               → Memory update (structured + embedding)        │
│               → HTML or Markdown output                       │
└──────────────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Explicit Data Flow**: Every graph node defines typed `Command[goto, update]` — no hidden state mutations
- **Structured Outputs**: All LLM decisions use Pydantic models (ConductResearch, ThinkTool, ComplexityAssessment, etc.)
- **Three-Tier Model Routing**: fast_llm for high-volume tasks, smart_llm for synthesis, strategic_llm for planning — plus per-task-type overrides
- **Subgraph Nesting**: Supervisor and Researcher are independently compiled StateGraphs with clear input/output boundaries
- **Mixed Compression**: Small → pass-through, Medium → embedding filter, Large → LLM semantic compression
- **Context Budget Enforcement**: "Sub-agents work in their own context, return only the summary"

### Key Capabilities

| System | Description |
|--------|-------------|
| **Deep Research Graph** | Multi-stage pipeline: clarify → plan → supervisor → N parallel researchers → report |
| **Adaptive Routing** | Complexity classification routes simple queries to fast direct-answer, complex queries to full Orchestrator-Workers pipeline |
| **Three-Tier Models** | fast_llm / smart_llm / strategic_llm + 8 task-type overrides for fine-grained cost optimization |
| **HITL Plan Gate** | Research plan generated before execution, paused via LangGraph interrupt for user approve/revise/cancel |
| **Quality Evaluation** | Three levels: Level 1 fast auto-check → auto-revise, Level 2 9-dim weighted scoring, Level 3 4-dim deep eval with degradation detection |
| **Structured Reflection** | ThinkTool captures gaps/confidence/strategy, enabling high-confidence auto-completion |
| **Skills** | 27 built-in SKILL.md skills (academic review, data analysis, chart viz, etc.), public/custom categories, tool allowlisting |
| **Memory** | Per-user file-based storage + embedding semantic index, LLM-driven fact extraction, research history retrieval |
| **MCP** | OAuth support (client_credentials/refresh_token), deferred tool search, mtime cache invalidation |
| **Multimodal** | Vision support (view_image, extract_web_images), user-uploaded image injection, HTML report image embedding |
| **Sandbox** | E2B/Daytona isolation, opt-in host bash with command auditing, pipe-to-shell detection |
| **Middleware** | 4 core concerns (ToolErrorHandler, LoopDetector, TokenUsageTracker, MemoryMiddleware) + shared graph functions |
| **Channels** | Feishu/Lark IM integration via abstract Channel base class |
| **Tool Registry** | Dynamic registration/discovery, tag-based lookup, usage statistics, LangChain compatibility |

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
│   │   ├── middleware.py       # ToolErrorHandler, LoopDetector, TokenUsageTracker, MemoryMiddleware
│   │   └── prompts.py          # resolve_prompt() — template-based prompt loading
│   ├── workflows/              # Deep research pipeline nodes
│   │   ├── input_gateway.py    # clarify_with_user → write_research_brief → classify_complexity
│   │   ├── research_plan.py    # HITL plan generation with LangGraph interrupt
│   │   ├── supervisor.py       # Supervisor subgraph (orchestrator loop + parallel researcher spawn)
│   │   ├── researcher.py       # Researcher subgraph (ReAct loop + mixed compression)
│   │   ├── report.py           # Final report + HTML/Markdown + image injection + quality check
│   │   ├── quality_check.py    # Level 1 instant quality validation + auto-revise
│   │   ├── evaluation.py       # Level 2 (9-dim) and Level 3 (4-dim deep) evaluation
│   │   ├── multimodal.py       # Image injection into LLM context
│   │   ├── source_routing.py   # ResearchSourceRoutingPolicy (web/rag/hybrid/mcp modes)
│   │   ├── continuation.py     # Auto-continuation handler for multi-turn tool calling
│   │   └── agent_factory.py    # build_writer_agent(), build_tool_agent() with middleware
│   ├── runtime/                # Lightweight runtime harness
│   │   ├── context.py          # RuntimeContext dataclass (thread_id, user_id, model, channel)
│   │   ├── sandbox_policy.py   # Command auditing, pipe-to-shell detection, audit logging
│   │   ├── user_context.py     # Per-request user_id resolution via ContextVar
│   │   ├── middleware/shared.py # Shared graph-middleware: loop check, token tracking, context budget
│   │   └── memory/             # Per-user memory (structured + embedding dual-mode)
│   │       ├── system.py       # MemorySystem: load/save/record/get_relevant_context
│   │       ├── storage.py      # File-based per-user JSON storage
│   │       ├── queue.py        # Debounced async update queue
│   │       └── updater.py      # LLM-driven fact extraction
│   ├── skills/                 # Skills system
│   │   ├── types.py            # Skill dataclass, SkillCategory enum (PUBLIC/CUSTOM)
│   │   ├── parser.py           # SKILL.md YAML frontmatter parser
│   │   ├── prompt.py           # Skill system-prompt section builder with progressive loading
│   │   ├── tool_policy.py      # Skill-based tool allowlist enforcement
│   │   ├── validation.py       # Skill content validation
│   │   └── installer.py        # Skill installation from remote sources
│   ├── mcp/oauth.py            # MCP OAuth token manager + interceptor
│   ├── prompts/                # Prompt templates (agent_prompts.py, system_prompts.py)
│   ├── tools/                  # Agent-side tools (view_image, extract_web_images, skill_manage)
│   └── api/                    # API sub-routers (documents, tracing)
├── common/                     # Shared utilities
│   ├── config.py               # Pydantic Settings — single source of truth for all config
│   ├── rate_limiter.py         # Token-bucket rate limiter
│   ├── stream_registry.py      # Active SSE stream tracker
│   ├── tracing.py              # Request tracing with OpenTelemetry spans
│   ├── cancellation.py         # Cooperative task cancellation
│   ├── session_manager.py      # Session lifecycle management
│   └── ...
├── tools/                      # Tool implementations (16+ categories)
│   ├── core/                   # Tool registry, base types, wrappers, MCP clients
│   ├── search/                 # Tavily, ArXiv, PubMed, Semantic Scholar, fallback web search
│   ├── sandbox/                # E2B/Daytona sandbox: shell, files, browser, vision, sheets, etc.
│   ├── browser/                # CDP-based browser automation
│   ├── code/                   # Python code executor (local + enhanced)
│   ├── crawl/                  # Web crawling (Crawl4AI)
│   ├── rag/                    # Document loader, embedder, vector store
│   ├── planning/               # Planning tool
│   ├── export/                 # Markdown converter
│   └── research/               # Content fetcher, reader client, page cache
├── skills/                     # Skill definitions
│   ├── public/                 # 27 built-in skills (academic review, data analysis, etc.)
│   └── custom/                 # User-authored skills directory
├── channels/                   # IM channel integration
│   ├── base.py                 # Abstract Channel ABC
│   ├── feishu.py               # Feishu/Lark WebSocket + card streaming
│   ├── manager.py              # ChannelManager dispatcher
│   ├── message_bus.py          # Async pub/sub message bus
│   └── service.py              # Channel lifecycle service
├── web/                        # Next.js frontend with SSE streaming, research tree viz
├── sdk/                        # Python + TypeScript SDKs
├── prompts/templates/          # YAML prompt templates (deepresearch, deepsearch, optimizer)
├── scripts/                    # CLI tools, smoke tests, secret scan
├── data/                       # Runtime data (memory, collaboration)
├── main.py                     # FastAPI application (50+ endpoints)
├── start_weaver.sh             # Startup script
└── Makefile
```

---

## Quick Start

```bash
git clone https://github.com/LittleSongxx/Weaver_pro.git
cd weaver

# Start script auto-generates .env and config on first run
./start_weaver.sh
```

Minimum `.env` configuration:
```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com   # or any OpenAI-compatible provider
TAVILY_API_KEY=tvly-...                     # web search API key
```

Access:
- **Web UI**: `http://127.0.0.1:3100`
- **API**: `http://127.0.0.1:8001`
- **API Docs**: `http://127.0.0.1:8001/docs`

---

## Deep Research Pipeline

### Flow

1. **Clarify** (`clarify_with_user`) — Structured clarification analysis. If the user query is ambiguous, returns a clarifying question and ends. Otherwise proceeds.

2. **Research Brief** (`write_research_brief`) — Transforms user messages into a structured brief. Active Skills' methodology is injected as guidance context.

3. **Complexity Classification** (`classify_complexity`) — Routes to fast direct-answer (simple queries) or full Orchestrator-Workers pipeline (deep queries).

4. **Research Plan** (`plan_research`) — [HITL] Generates a structured plan (sub-topics, search strategy, source preferences, expected output) and pauses via LangGraph interrupt for user approval.

5. **Supervisor Loop** (`supervisor ⇄ supervisor_tools`) — The supervisor LLM calls structured tools to manage the research:
   - `ConductResearch` — spawns N parallel Researcher subgraphs
   - `ThinkTool` — structured reflection (gaps, confidence, next strategy)
   - `SourceCurate` — ranks collected sources by quality
   - `ResearchComplete` — ends the loop when confidence is high

6. **Researcher Subgraphs** (N parallel instances) — Each researcher executes an independent ReAct loop with search, sandbox, and MCP tools, then compresses findings via mixed strategy.

7. **Report Generation** (`final_report_generation`) — Synthesizes all findings into Markdown or HTML report with automatic image embedding, quality checking, and 3-retry token-limit handling.

### Model Routing

The platform uses a three-tier model strategy inspired by Anthropic's "Building Effective Agents":

| Tier | Default Model | Use Case |
|------|--------------|----------|
| `fast_llm` | qwen3.6-flash | Summarization, quality checks, simple classification |
| `smart_llm` | qwen3.6-plus | Research synthesis, report writing, compression |
| `strategic_llm` | qwen3.7-max | Planning, strategy decisions, deep analysis |

All model names are configurable via `FAST_LLM_MODEL` / `SMART_LLM_MODEL` / `STRATEGIC_LLM_MODEL` in `.env`.

Plus **8 task-type specific overrides**: query_generation, content_summarization, web_reading, result_synthesis, strategic_decision, compression, report_writing, quality_check.

### Compression Strategy

| Content Size | Strategy | Technique |
|-------------|----------|-----------|
| < 8K chars | Raw pass-through | Zero-cost |
| 8K–50K chars | Embedding similarity filter | text-embedding-3-small → relevance threshold |
| > 50K chars | LLM semantic compression | smart_llm rewrites findings |

### Quality Evaluation

| Level | Model | Dimensions | Action |
|-------|-------|-----------|--------|
| Level 1 | fast_llm | 6-dim (citation density, section completeness, format, relevance, length, evidence alignment) | Auto-revise up to 2× |
| Level 2 | smart_llm | 9-dim weighted scoring (binary pass/fail) | Post-report evaluation |
| Level 3 | strategic_llm | 4-dim (coverage, accuracy, freshness, coherence) + degradation detection | Deep quality monitoring |

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

---

## Configuration

Key settings in `.env`:

```bash
# Core
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
PRIMARY_MODEL=qwen3.6-plus
REASONING_MODEL=qwen3.7-max

# Three-Tier Model Routing (configured via Settings class)
FAST_LLM_MODEL=qwen3.6-flash
SMART_LLM_MODEL=qwen3.6-plus
STRATEGIC_LLM_MODEL=qwen3.7-max

# Per-phase model overrides (empty = use tier default)
RESEARCH_MODEL=
COMPRESSION_MODEL=
SUMMARIZATION_MODEL=
FINAL_REPORT_MODEL=

# Research limits
MAX_CONCURRENT_RESEARCH=5
MAX_RESEARCHER_ITERATIONS=6
MAX_REACT_TOOL_CALLS=8

# Report
REPORT_FORMAT=markdown          # or "html"
HTML_REPORT_EMBED_IMAGES=false
MAX_REPORT_REVISIONS=2

# Vision / Multimodal
SUPPORTS_VISION=false
VISION_ENRICH_DATA=false

# Memory
MEMORY_ENABLED=false
WEAVER_MEMORY_PATH=data/memory

# MCP
MCP_ENABLED=false

# Sandbox
SANDBOX_MODE=e2b                 # e2b | daytona | none
HOST_BASH_ENABLED=false

# Channels
CHANNELS_ENABLED=false
FEISHU_CHANNEL_ENABLED=false

# Skills
SKILLS_PUBLIC_DIR=skills/public
SKILLS_CUSTOM_DIR=skills/custom

# Security
INTERNAL_API_KEY=                # Set to enable internal auth
TOOL_APPROVAL=false              # Enable HITL for risky tools
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

27 built-in skills cover: academic paper review, data analysis, chart visualization, frontend design, HTML report generation, newsletter generation, podcast generation, research trend analysis, science communication, and more.

Skills support:
- **Progressive Loading**: Agent sees skill list in prompt, loads full content on demand
- **Tool Allowlisting**: Each skill declares which tools the agent may use
- **Skill Evolution**: Agents can create/modify skills via `skill_manage` tool

---

## License

MIT License. See `LICENSE`.
