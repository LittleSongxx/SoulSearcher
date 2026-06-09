# Weaver

LangGraph-based deep research platform with a typed workflow, supervised parallel researchers,
research to-do tracking, quality gates, memory, MCP integration, and a Next.js research workspace.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.134+-teal)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/License-MIT-yellow)

## What Weaver Does

Weaver turns a complex question into a controlled deep-research workflow:

```text
User query
  -> input gateway
  -> clarification or research brief
  -> complexity routing
  -> HITL research plan
  -> workflow to-do list
  -> supervisor loop
  -> parallel researcher subgraphs
  -> evidence extraction and compression
  -> report generation
  -> quality checks, optional revision
  -> SSE stream, session evidence, saved artifacts
```

It is built for long-running research tasks where the system needs to keep a clear objective,
spawn focused investigations, preserve evidence, and return a traceable final report.

## Current Core Features

- **Typed LangGraph workflow**: explicit `AgentState`, `SupervisorState`, and `ResearcherState`
  carry data across the graph.
- **DeepResearch pipeline**: clarify, brief, classify, plan, supervise, research, report, evaluate.
- **Human-in-the-loop plan gate**: generated research plans can be approved, revised, or cancelled
  before expensive execution starts.
- **Workflow-native research to-dos**: approved plans are converted into internal tasks that guide
  the supervisor and are exposed through SSE and evidence APIs.
- **Supervisor-workers execution**: the supervisor coordinates `ConductResearch`, `ThinkTool`, and
  `ResearchComplete`; researcher subgraphs run focused ReAct loops and return compressed findings.
- **Three-tier model routing**: fast, smart, and strategic model tiers, plus per-task overrides.
- **Search and source routing**: web, academic, feed, browser/crawl, MCP, sandbox/code, image, and
  export tools are routed according to task needs and provider support.
- **Quality gates**: citation coverage, claim verification, rubric scoring, auto-revision, and
  optional deeper evaluation.
- **Memory**: per-user research memory can be recorded after reports and injected into later turns.
- **Skills**: public and custom `SKILL.md` skills with allowlists, history, installation, and editing
  APIs.
- **Frontend workspace**: streaming research UI, plan interrupt handling, evidence/artifacts panels,
  session history, comments, versions, export, and trace views.

## Architecture

```text
main.py
  FastAPI app, CORS, auth/rate-limit middleware, SSE research endpoint

agent/core/
  graph.py              builds the main LangGraph
  state.py              typed state and structured tool schemas
  configuration.py      runtime research configuration
  model_routing.py      model tier and per-task routing
  llm_factory.py        provider-aware ChatOpenAI construction
  events.py             SSE event normalization and emission
  middleware.py         loop, token, error, and memory middleware
  search_cache.py       search result cache

agent/workflows/
  input_gateway.py      clarify, research brief, complexity classification
  research_plan.py      plan generation and interrupt/resume handling
  research_todo.py      workflow-native research to-do state
  supervisor.py         supervisor loop and parallel researcher dispatch
  researcher.py         researcher ReAct loop and mixed compression
  report.py             final report, artifacts, HTML/Markdown, image injection
  quality_check.py      fast validation and auto-revision
  evaluation.py         deeper rubric and degradation evaluation
  source_routing.py     web/academic/MCP source policy
  evidence_extractor.py evidence and source extraction
  source_registry.py    source scoring and registry

agent/runtime/
  context.py            runtime context object
  middleware/shared.py  shared graph middleware helpers
  memory/               per-user memory storage, queue, updater, prompts

tools/
  search/               web, academic, feed, provider reliability, cache support
  browser/, crawl/      browser and page extraction tools
  sandbox/, code/       controlled command and code execution tools
  planning/             planning-oriented utilities
  automation/           standalone automation tools
  export/, io/, data/   output and data utilities

web/
  Next.js workspace with streaming research, plan approval, evidence, artifacts,
  session collaboration, traces, and generated OpenAPI types
```

The current runtime uses the explicit DeepResearch workflow. The API and module map below describe
the routes and graph components that are active in this version.

## Research Workflow

### 1. Request Handling

The API receives a query through `/api/research/sse`. Before it reaches the graph, the backend
handles request authentication, rate limiting, cancellation registration, SSE keepalive, session
tracking, and event translation.

### 2. Input Gateway

The gateway decides whether the request needs clarification, can be answered directly, or should
enter deep research. For deep research it builds a normalized research brief and classifies task
complexity.

### 3. Research Plan

The plan node generates a Markdown plan and pauses with a LangGraph interrupt. The frontend can
approve, revise, or cancel the plan through the interrupt APIs. Revised plans are fed back into
the workflow before execution continues.

### 4. Research To-Dos

After plan approval, Weaver derives up to eight internal to-do items from the plan, preferring the
`Sub-topics` section when present. These tasks are soft control state, not a user-editable project
manager. They help the supervisor stay aligned during long-running research.

To-dos are updated as the workflow progresses:

- a matching pending task becomes `running` before `ConductResearch`;
- it becomes `completed` when the researcher returns useful findings;
- supervisor-discovered topics can create dynamic tasks;
- `ThinkTool` gaps can create pending gap tasks;
- quality follow-up research can append follow-up tasks;
- updates stream as `task_update` SSE events and are saved in session evidence.

### 5. Supervisor Loop

The supervisor receives the research brief, plan, completed research summaries, and compact to-do
status. It chooses among structured actions:

- `ConductResearch`: start one or more focused investigations;
- `ThinkTool`: reflect on gaps, confidence, and next strategy;
- `ResearchComplete`: finish when enough evidence has been gathered.

The to-do list is a soft constraint: the supervisor is prompted to prioritize unfinished tasks, but
it can still pursue new directions when the evidence calls for it.

### 6. Researcher Subgraphs

Each researcher runs in its own context. It searches, browses, crawls, calls approved tools, reads
images when needed, and compresses findings before returning them to the supervisor. This keeps
the supervisor context small while preserving source-backed summaries.

### 7. Report And Quality

The report node synthesizes the final answer, extracts evidence, builds artifacts, injects useful
images when available, and runs quality checks. Fast quality failure can trigger automatic revision;
deeper evaluation and claim verification are available through configuration.

### 8. Response And Artifacts

The user receives streamed events during execution and a final report at completion. Session APIs
can later return state, evidence, sources, artifacts, research to-dos, todo summary, traces,
comments, versions, and export output.

## API Surface

The active backend routes are grouped around research execution, session persistence, skills,
memory, search/tool status, export, channels, and traces.

### Research And Interrupts

- `POST /api/research/sse` - run a streaming research request
- `POST /api/research/cancel/{thread_id}` - cancel one running request
- `POST /api/research/cancel-all` - cancel all running requests
- `POST /api/research/fork` - fork an existing research thread
- `GET /api/interrupt/{thread_id}/status` - get pending interrupt state
- `POST /api/interrupt/{thread_id}/resume` - resume a specific interrupted thread
- `POST /api/interrupt/resume` - generic interrupt resume endpoint

### Health, Config, And Runs

- `GET /` - service root
- `GET /health` - backend health check
- `GET /api/health/agent` - graph/runtime health details
- `GET /api/config/public` - public frontend configuration
- `GET /api/runs` - list in-memory run metrics
- `GET /api/runs/{thread_id}` - get one run's metrics
- `GET /metrics` - Prometheus metrics when enabled

### Sessions And Evidence

- `GET /api/sessions` - list sessions
- `GET /api/sessions/{thread_id}` - get one session
- `DELETE /api/sessions/{thread_id}` - delete one session
- `GET /api/sessions/{thread_id}/state` - inspect graph/session state
- `GET /api/sessions/{thread_id}/evidence` - get evidence, sources, artifacts, and research to-dos
- `POST /api/sessions/{thread_id}/resume` - resume a session
- `POST /api/sessions/{thread_id}/continue-research` - continue from an existing report
- `POST /api/sessions/{thread_id}/share` - create a shared view
- `GET /api/share/{share_id}` - read shared session data
- `DELETE /api/share/{share_id}` - remove a shared view
- `GET /api/sessions/{thread_id}/comments` - list comments
- `POST /api/sessions/{thread_id}/comments` - add a comment
- `GET /api/sessions/{thread_id}/versions` - list saved versions
- `POST /api/sessions/{thread_id}/versions` - create a version
- `POST /api/sessions/{thread_id}/restore/{version_id}` - restore a version

### Skills, Memory, Tools, Export, Traces

- `GET /api/skills`, `GET /api/skills/{name}`, `PUT /api/skills/{name}`
- `POST /api/skills/install`
- `GET /api/skills/custom`, `GET /api/skills/custom/{name}`
- `PUT /api/skills/custom/{name}`, `DELETE /api/skills/custom/{name}`
- `GET /api/skills/custom/{name}/history`, `POST /api/skills/custom/{name}/rollback`
- `GET /api/memory`, `POST /api/memory/reload`, `GET /api/memory/status`
- `GET /api/tasks/active`
- `GET /api/tools/registry`
- `GET /api/search/providers`, `POST /api/search/providers/reset`
- `GET /api/search/cache/stats`, `POST /api/search/cache/clear`
- `GET /api/export/templates`, `GET /api/export/{thread_id}`
- `GET /api/channels`, `POST /api/channels/{name}/restart`
- `GET /api/traces/{thread_id}`, `GET /api/traces/{thread_id}/all`
- `GET /api/traces/{thread_id}/summary`

## Frontend

The `web/` app is a Next.js 14 workspace. It supports:

- streaming research through SSE;
- research-plan approval and revision interrupts;
- thinking/process event display, including `task_update`;
- evidence, source, artifact, and research todo panels;
- session history, comments, sharing, versions, and restore;
- export and trace inspection;
- generated TypeScript API types from backend OpenAPI.

## Configuration

Settings are defined in `common/config.py` and can be supplied through environment variables.
The most important groups are:

```bash
# LLM providers
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
AZURE_OPENAI_API_KEY=...
DASHSCOPE_API_KEY=...

# Model routing
PRIMARY_MODEL=qwen-plus
REASONING_MODEL=qwen-plus
FAST_LLM_MODEL=qwen-turbo
SMART_LLM_MODEL=qwen-plus
STRATEGIC_LLM_MODEL=qwen-plus
PLANNER_MODEL=...
RESEARCHER_MODEL=...
WRITER_MODEL=...
EVALUATOR_MODEL=...

# Search providers
TAVILY_API_KEY=...
BOCHA_API_KEY=...
SERPER_API_KEY=...
SERPAPI_API_KEY=...
BING_API_KEY=...
BRAVE_API_KEY=...
EXA_API_KEY=...
FIRECRAWL_API_KEY=...
GOOGLE_SEARCH_API_KEY=...
GOOGLE_SEARCH_ENGINE_ID=...

# Runtime behavior
ENABLE_MEMORY=true
ENABLE_MCP=false
HUMAN_REVIEW=true
TOOL_APPROVAL=false
DEEPSEARCH_MODE=auto

# API and operations
WEAVER_INTERNAL_API_KEY=...
DATABASE_URL=sqlite:///./data/weaver.db
ENABLE_PROMETHEUS=false
```

DeepSearch settings control supervisor rounds, researcher parallelism, context compression,
search freshness, claim verification, citation gates, summary triggers, and guardrails. See
`common/config.py` for the complete current list.

## Skills

The repository includes 20 public skills under `skills/public/`, plus custom skills under
`skills/custom/`. Skills are Markdown-based instructions with declared tool allowlists and are
loaded by the runtime skill system. The API supports listing, reading, editing, installing,
history, and rollback for custom skills.

## Development

### Backend

```bash
# Install project dependencies
make setup

# Run the backend
python main.py

# Hot reload
DEBUG=true WEAVER_RELOAD=1 python main.py
```

### Frontend

```bash
cd web
npm install
npm run dev
```

### Tests And Checks

```bash
# Python tests
conda run -n weaver python -m pytest tests/ -q

# Frontend type check
cd web
npx tsc --noEmit --pretty false

# OpenAPI TypeScript generation
npm run api:types

# Repository verification helpers
make test
make lint
make verify
```

`make verify` runs compile checks, pytest, OpenAPI type generation, live API smoke checks, and the
frontend end-to-end test hook.

## Data And Runtime Files

Runtime files are stored under `data/` when local defaults are used. This includes session-related
state, memory files, traces, collaboration metadata, and generated artifacts. Secrets and local
credentials should stay in environment variables or ignored local config files.

## License

MIT License. See [LICENSE](LICENSE).
