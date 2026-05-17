<div align="center">

# Weaver — AI Super Agent Platform

**LangGraph-based · Lead Agent + Subagent Orchestration · Deep Research · Skills & MCP · Persistent Memory · Sandbox Isolation · Multi-IM Channels**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-7B68EE?style=flat&logo=databricks&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat)

</div>

---

## Architecture

Weaver is a **Lead Agent + Subagent** platform built on LangGraph, aligned with the DeerFlow agent runtime pattern:

```
┌──────────────────────────────────────────────────────────┐
│                    IM Channels                            │
│   Feishu/Lark · Slack · Telegram · DingTalk · WeCom       │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│                  FastAPI Gateway (:8001)                   │
│   /api/research/sse · /api/channels · /api/skills         │
│   /api/memory · /api/mcp · /api/models                    │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│              Lead Agent (12-layer Middleware)              │
│  DynamicContext → Summarization → Todo → Title → Memory   │
│  → SubagentLimit → LoopDetection → ToolError → Sandbox   │
│  → Clarification                                          │
└──────────────────────┬───────────────────────────────────┘
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Research │ │  Coder   │ │ General  │  ... task tool
    │ Subagent │ │ Subagent │ │ Purpose  │
    └──────────┘ └──────────┘ └──────────┘
           │           │           │
           └───────────┼───────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│                    Tool System                             │
│  Built-in Tools · MCP Tools · Community Tools · Skills    │
│  Sandbox (E2B/Daytona) · Deferred Tool Search             │
└──────────────────────────────────────────────────────────┘
```

### Key Capabilities

| System | Description |
|--------|-------------|
| **Lead Agent** | Dynamic model selection, thinking/vision toggle, skills-aware tool filtering |
| **Subagents** | Dual thread-pool executor, isolated contexts, cooperative cancellation, 15-min timeout |
| **Skills** | `SKILL.md` YAML frontmatter, public/custom categories, on-demand loading, allowed-tools filtering |
| **MCP** | OAuth support (client_credentials/refresh_token), mtime cache invalidation, deferred tool search |
| **Memory** | LLM-driven fact extraction, debounced update queue (30s), per-user per-agent isolation, 5 fact categories |
| **Context Governance** | 12-layer middleware chain, static system prompt + `<system-reminder>` injection, auto-summarization |
| **Sandbox** | SandboxProvider abstraction (E2B/Daytona), host bash opt-in security gating, command auditing |
| **Guardrails** | Pre-tool-call authorization, pluggable provider protocol, built-in allowlist/denylist |
| **Channels** | 5+ IM platforms (Feishu, Slack, Telegram, DingTalk, WeCom), streaming card updates, file receive/send |
| **Deep Research** | Multi-strategy (tree/supervisor_workers), HITL gates, CJK-aware token estimation, claim verification |

---

## Quick Start

```bash
git clone https://github.com/skygazer42/weaver.git
cd weaver

# Start script auto-generates .env and config files on first run
./start_weaver.sh
```

Minimum `.env` configuration:
```bash
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com  # or your provider
TAVILY_API_KEY=tvly-...
```

Access:
- **Web UI**: `http://127.0.0.1:3100`
- **API**: `http://127.0.0.1:8001`
- **API Docs**: `http://127.0.0.1:8001/docs`

---

## Project Structure

```
Weaver/
├── agent/
│   ├── runtime/              # DeerFlow-aligned runtime harness
│   │   ├── lead_agent.py     # Lead agent factory with middleware chain
│   │   ├── subagents.py      # Subagent executor (dual thread pool)
│   │   ├── task_tool.py      # task() delegation tool
│   │   ├── tool_registry.py  # Unified tool assembly
│   │   ├── tool_search.py    # Deferred MCP tool discovery
│   │   ├── context.py        # RuntimeContext dataclass
│   │   ├── state.py          # ThreadState schema
│   │   ├── events.py         # Legacy SSE event helpers
│   │   ├── mcp_cache.py      # MCP tool cache with mtime invalidation
│   │   ├── sandbox_policy.py # Sandbox security policies
│   │   ├── sandbox_provider.py # SandboxProvider abstraction
│   │   ├── guardrails.py     # Pre-tool-call authorization
│   │   ├── user_context.py   # Per-request user_id resolution
│   │   ├── deep_research_tool.py  # DeepSearch wrapped as LangChain tool
│   │   ├── middleware/       # 12 middleware components
│   │   │   ├── dynamic_context.py  # <system-reminder> injection
│   │   │   ├── summarization.py    # Context auto-summarization
│   │   │   ├── loop_detection.py   # Tool-call loop breaking
│   │   │   ├── clarification.py    # ask_clarification interception
│   │   │   ├── memory_ware.py      # Conversation → memory queue
│   │   │   ├── subagent_limit.py   # Max concurrent task enforcement
│   │   │   ├── todo_ware.py        # Plan mode task tracking
│   │   │   ├── title_ware.py       # Auto thread title generation
│   │   │   ├── thread_data.py      # Per-thread isolation
│   │   │   ├── uploads_ware.py     # Uploaded file injection
│   │   │   ├── sandbox_audit.py    # Security audit logging
│   │   │   └── tool_error.py       # Tool exception → ToolMessage
│   │   └── memory/           # Long-term memory system
│   │       ├── storage.py    # File-based per-user storage
│   │       ├── queue.py      # Debounced update queue
│   │       ├── updater.py    # LLM-driven memory extraction
│   │       └── prompt.py     # Memory update prompts
│   ├── skills/               # Skills system
│   │   ├── types.py          # Skill data model
│   │   ├── parser.py         # SKILL.md YAML frontmatter parser
│   │   ├── storage.py        # Local skill storage + enabled state
│   │   └── tool_policy.py    # Skill-based tool allowlist filtering
│   ├── mcp/                  # MCP integration
│   │   └── oauth.py          # OAuth token manager + interceptor
│   ├── core/                 # Core graph, state, LLM factory
│   ├── workflows/            # DeepSearch pipeline + agent tools
│   └── prompts/              # System prompts
├── channels/                 # IM channel integration
│   ├── base.py               # Abstract Channel base class
│   ├── feishu.py             # Feishu/Lark WebSocket + card streaming
│   ├── manager.py            # ChannelManager dispatcher
│   ├── message_bus.py        # Async pub/sub message bus
│   ├── service.py            # Channel lifecycle service
│   ├── store.py              # Thread mapping persistence
│   └── commands.py           # Shared command definitions
├── common/                   # Shared utilities
│   ├── config.py             # Pydantic Settings (30+ new fields)
│   ├── rate_limiter.py       # Token-bucket rate limiter
│   └── stream_registry.py    # Active SSE stream tracker
├── tools/                    # Tool implementations
├── web/                      # Frontend (Next.js)
├── skills/                   # Skill definitions (SKILL.md)
├── config/                   # Config files
├── main.py                   # FastAPI application entry point
├── start_weaver.sh           # Startup script
└── Makefile
```

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/research/sse` | Main research SSE endpoint (streaming) |
| `GET /api/channels` | IM channel status |
| `POST /api/channels/{name}/restart` | Restart a channel |
| `GET /api/skills` | List skills (supports `?enabled_only=true`) |
| `PUT /api/skills/{name}` | Enable/disable a skill |
| `GET /api/memory` | Get memory data for effective user |
| `POST /api/memory/reload` | Force reload memory from disk |
| `GET /api/mcp/config` | MCP server configuration |
| `PUT /api/mcp/config` | Update MCP configuration |
| `GET /api/models` | List available models |
| `GET /api/health` | Health check with persistence mode info |

---

## Configuration

Key DeerFlow-aligned settings in `.env`:

```bash
# Agent Runtime
AGENT_RUNTIME_ENABLED=true
AGENT_RUNTIME_DEFAULT_SUBAGENT_ENABLED=true
AGENT_RUNTIME_MAX_CONCURRENT_SUBAGENTS=3

# Memory
MEMORY_ENABLED=true
MEMORY_INJECTION_ENABLED=true
MEMORY_DEBOUNCE_SECONDS=30
MEMORY_MAX_FACTS=100
MEMORY_FACT_CONFIDENCE_THRESHOLD=0.7
MEMORY_MAX_INJECTION_TOKENS=2000

# Skills
SKILLS_PUBLIC_DIR=skills/public
SKILLS_CUSTOM_DIR=skills/custom

# Summarization
SUMMARIZATION_ENABLED=true
SUMMARIZATION_MAX_INPUT_TOKENS=64000
SUMMARIZATION_KEEP_LAST=15

# Loop Detection
LOOP_DETECTION_ENABLED=true
LOOP_DETECTION_MAX_REPEATS=3

# Guardrails
GUARDRAILS_ENABLED=false
GUARDRAILS_DENYLIST=

# Channels
CHANNELS_ENABLED=false
FEISHU_CHANNEL_ENABLED=false
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# Tool Search (deferred MCP tools)
TOOL_SEARCH_ENABLED=false

# Sandbox
SANDBOX_MODE=e2b
SANDBOX_ALLOW_HOST_BASH=false
HOST_BASH_ENABLED=false
```

---

## License

MIT License. See `LICENSE`.
