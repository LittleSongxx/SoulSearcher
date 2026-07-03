# SoulSearcher

[中文](README.md) | Default README: Chinese

SoulSearcher is a LangGraph-based deep research agent platform. It turns open-ended questions into auditable research runs: clarify the request, create a research brief, pause for plan approval, execute a Plan DAG, coordinate parallel researchers, collect evidence, write the report, run quality checks, and expose the full process through SSE, Evidence Panel, and Run Events.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.134+-teal)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/License-MIT-yellow)

## In One Sentence

SoulSearcher is not a one-shot chatbot and not just RAG. It is a **Workflow + Agent** system for complex research: deterministic steps are controlled by code, open-ended exploration is handled by models and tools, and every important state transition is recorded.

```mermaid
flowchart LR
    Q["User question"] --> C["Clarify and brief"]
    C --> P["Human-approved plan"]
    P --> D["Plan DAG"]
    D --> S["Supervisor"]
    S --> R1["Researcher A"]
    S --> R2["Researcher B"]
    S --> R3["Researcher C"]
    R1 --> E["Evidence pool"]
    R2 --> E
    R3 --> E
    E --> W["Report writer"]
    W --> G["Quality gates"]
    G --> O["Report / Evidence / Event replay"]
```

## Core Capabilities

| Area | Current Implementation |
| --- | --- |
| Explicit workflow | `clarify -> brief -> classify -> plan -> supervisor -> researcher -> report -> evaluate` |
| Human plan gate | LangGraph interrupt pauses the run until the plan is approved, revised, or cancelled |
| Plan DAG | Tasks have dependencies, frontier, status, evidence links, result previews, and replan events |
| Parallel researchers | The supervisor dispatches multiple isolated researcher subgraphs |
| Unified retrieval | `RetrievalPolicy` controls public web, private library, user-provided sources, connectors, methods, profiles, and budgets |
| Evidence | Evidence items, passages, source registry, citation annotations, claim support |
| Quality | Citation gate, claim verifier, rubric evaluation, automatic revision, follow-up research |
| Run events | Thread-ordered events are persisted and replayable through REST or replay + live-tail SSE; clients can reconnect with a cursor |
| A2A 1.0 Server | Publishes `/.well-known/agent-card.json` and exposes JSON-RPC DeepResearch on `/api/a2a`; in Temporal mode `SendMessage/SendStreamingMessage` starts a background workflow and returns `working` quickly |
| Memory | Optional memory service for findings, entities, relations, and procedural lessons |
| Skills | Public/custom skills with allowlists, validation, storage, history, and rollback |
| Frontend | Next.js workspace for streaming research, plan review, evidence, Plan DAG, events, artifacts, sessions, and traces |

## Architecture

```mermaid
flowchart TB
    subgraph Frontend["web / Next.js"]
        UI["Research Workspace"]
        EvidencePanel["EvidencePanel\nSources / Claims / Plan / Events / Gaps"]
        LibraryUI["Document Library"]
    end

    subgraph API["FastAPI / main.py"]
        ResearchAPI["/api/runs/background\n/api/runs/{thread_id}/events/sse"]
        InterruptAPI["/api/interrupt/*"]
        SessionAPI["/api/sessions/*"]
        RunAPI["/api/runs/*"]
        LibraryAPI["/api/library/*"]
        ExportTrace["export / traces / health"]
    end

    subgraph Core["agent/core"]
        Graph["LangGraph"]
        State["Typed State"]
        Routing["Model Routing"]
        Events["SSE Events"]
        Middleware["error / loop / token"]
    end

    subgraph Workflow["agent/workflows"]
        Gateway["Input Gateway"]
        Plan["Research Plan"]
        DAG["Plan Graph"]
        Supervisor["Supervisor"]
        Researcher["Researcher"]
        Report["Report"]
        Quality["Quality Check"]
    end

    subgraph Retrieval["agent/retrieval"]
        Policy["RetrievalPolicy"]
        Retrieve["retrieve_sources"]
        Read["read_source"]
        Documents["Document Library"]
    end

    subgraph Runtime["agent/runtime"]
        Runs["RunManager"]
        Background["Background Runs"]
        RequestBuilder["Request Builder"]
    end

    UI --> API
    EvidencePanel --> API
    API --> Core
    API --> Runtime
    Core --> Workflow
    Workflow --> Retrieval
    Workflow --> Runtime
    Retrieval --> Tools["tools/"]
```

## Repository Layout

```text
agent/core/
  graph.py              Main LangGraph assembly
  state.py              AgentState / SupervisorState / ResearcherState and tool schemas
  configuration.py      Runtime research configuration
  model_routing.py      Fast / smart / strategic model routing
  events.py             Canonical SSE events
  middleware.py         Tool error, loop, and token middleware

agent/workflows/
  input_gateway.py      Clarification, research brief, complexity classification
  research_plan.py      Research plan, interrupt/resume, Plan DAG initialization
  plan_graph.py         DAG creation, frontier computation, task status, replan actions
  supervisor.py         Supervisor loop and parallel researcher dispatch
  researcher.py         Researcher ReAct loop and retrieval policy injection
  report.py             Final report, artifacts, quality follow-up, Plan DAG replan
  interactive_continue.py  Continue-research requests as replan actions
  quality_check.py      Fast quality checks and automatic revision
  evaluation.py         Rubric and degradation evaluation

agent/retrieval/
  policy.py             RetrievalPolicy: origin / channel / method / profile / budget
  gateway.py            Unified retrieval gateway: retrieve_sources / read_source
  documents.py          User library parsing, chunking, and hybrid ranking

agent/runtime/
  runs.py               Run records, persisted run events, memory fallback
  background_runs.py    Background research jobs
  request_builder.py    request -> graph state/config

common/
  config.py             Settings source of truth
  evidence_store.py     Evidence/session response patches
  session_manager.py    Sessions, artifacts, Plan DAG, run-events

web/
  Next.js research workspace, EvidencePanel, document library, stream protocol, OpenAPI TS types
```

## Research Flow

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant G as LangGraph
    participant S as Supervisor
    participant R as Researchers
    participant E as Evidence Store
    participant UI as Web UI

    U->>API: POST /api/runs/background
    API->>API: Create or reuse background run / Temporal workflow
    API-->>UI: thread_id / run_id / workflow_id / status
    UI->>API: GET /api/runs/{thread_id}/events/sse?live=true
    API->>G: Build state/config and execute in the background
    G->>G: Clarify / brief / complexity route
    G-->>UI: interrupt: plan review required
    U->>API: approve / revise / cancel
    G->>G: Create Plan DAG
    G->>S: Enter supervisor loop
    S->>R: Parallel ConductResearch
    R->>E: Persist sources, evidence, passages
    S->>G: ThinkTool / ResearchComplete
    G->>E: Report, quality results, Plan DAG
    API-->>UI: SSE + Run Events + Artifacts
```

## Plan DAG

Plan DAG is the only canonical planning state. Compatibility fields such as `research_todos` are derived from the graph; they are not a second planning system.

```mermaid
flowchart LR
    A["approved/revised plan"] --> B["create_plan_graph"]
    B --> C["ready frontier"]
    C --> D["mark running"]
    D --> E{"research result"}
    E -->|enough evidence| F["completed"]
    E -->|failed or insufficient| G["blocked"]
    F --> H["recompute frontier"]
    G --> I["ThinkTool / quality follow-up"]
    I --> J["apply replan actions"]
    J --> H
    H --> K{"all tasks closed?"}
    K -->|no| C
    K -->|yes| L["report"]
```

Tasks include `id`, `title`, `question`, `deps`, `status`, `priority`, `retrieval_policy_hint`, `evidence_ids`, `claim_ids`, `blocked_reason`, and `result_preview`. Updates emit `plan_graph_update` events and are saved into session/evidence artifacts.

## RetrievalPolicy

SoulSearcher keeps one retrieval policy model. Deprecated source-routing payloads are rejected at API/runtime boundaries so the project does not maintain parallel routing designs.

```mermaid
flowchart TB
    Request["Research Request"] --> Policy["RetrievalPolicy"]
    Policy --> Origins["allowed_origins\npublic_web / private_corpus / user_provided / external_system"]
    Policy --> Channels["channels\nsearch_api / browser / crawler / file_upload / mcp / connector"]
    Policy --> Methods["methods\nweb_search / academic_search / vector_search / keyword_search / crawl / deep_read"]
    Policy --> Budget["budget\nmax results / read chars / per-origin limits"]
    Origins --> Gateway["Retrieval Gateway"]
    Channels --> Gateway
    Methods --> Gateway
    Budget --> Gateway
    Gateway --> Evidence["RetrievedSource + EvidenceItem"]
```

## Run Events

| Endpoint | Purpose |
| --- | --- |
| `GET /api/runs` | List runs |
| `GET /api/runs/{thread_id}` | Run metrics and status |
| `GET /api/runs/{thread_id}/events?after_seq=0` | Fetch persisted events after a sequence number |
| `GET /api/runs/{thread_id}/events/sse?after_seq=0&live=true` | Replay persisted events and live-tail new events; supports `Last-Event-ID`, heartbeat, and reconnect |
| `POST /api/runs/background` | Submit or idempotently reuse a background research run; Temporal mode returns `workflow_id/execution_backend/status` |
| `GET /api/runs/{thread_id}/background` | Inspect background run state |
| `POST /api/runs/{thread_id}/background/cancel` | Cancel a background run; Temporal mode records workflow cancel/signal state |

Postgres-backed runs auto-create the `soulsearcher_run_events` table. Local and test runs can fall back to memory storage.

## API Groups

| Group | Routes |
| --- | --- |
| Research | Deep research defaults to `POST /api/runs/background` + events SSE; `POST /api/research/sse` remains for short-session compatibility |
| Interrupt | interrupt status and canonical resume |
| Sessions | session list/detail/state/evidence/resume/continue/delete |
| Runs | run list, metrics, background, events, event replay, source injection |
| Library/Retrieval | document library, document search, retrieval search |
| Collaboration | share links, comments, versions, restore |
| Skills | public/custom skills list, read, update, install, history, rollback |
| Memory | records, retrieve, graph, status, skill evolution |
| Tools/Search | active tasks, tool registry, providers, cache stats/reset/clear |
| Export/Traces | report export, templates, trace detail, trace summary |
| Health/Ops | `/`, `/health`, `/api/health/agent`, `/api/config/public`, `/metrics` |

## Frontend Workspace

```mermaid
flowchart TB
    Workspace["Research Workspace"] --> Background["/api/runs/background"]
    Background --> Stream["Event replay + live-tail SSE"]
    Workspace --> Approval["Plan approval / revision"]
    Workspace --> Thinking["Thinking Process"]
    Workspace --> Evidence["EvidencePanel"]
    Evidence --> Sources["Sources"]
    Evidence --> Claims["Claims"]
    Evidence --> Plan["Plan DAG"]
    Evidence --> Events["Run Events"]
    Evidence --> Gaps["Gaps"]
    Workspace --> Artifacts["Markdown / HTML / Export"]
    Workspace --> Sessions["Sessions / Share / Comments / Versions"]
    Workspace --> Trace["Trace Viewer"]
```

## Configuration

Settings live in `common/config.py` and can be overridden with environment variables:

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

# Runtime
MEMORY_ENABLED=true
MEMORY_BACKEND=postgres
MEMORY_DATABASE_URL=
ENABLE_MCP=false
HUMAN_REVIEW=true
TOOL_APPROVAL=false
DEEPSEARCH_MODE=auto
BACKGROUND_RUNS_ENABLED=true
SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND=temporal
TEMPORAL_ADDRESS=127.0.0.1:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=soulsearcher-deep-research

# API / Ops
SOULSEARCHER_INTERNAL_API_KEY=...
DATABASE_URL=sqlite:///./data/soulsearcher.db
ENABLE_PROMETHEUS=false
```

## A2A 1.0 Server

SoulSearcher exposes DeepResearch as an A2A 1.0 JSON-RPC server:

```text
GET  /.well-known/agent-card.json
POST /api/a2a
```

The Agent Card advertises `supportedInterfaces[0]` with `protocolBinding="JSONRPC"`, `protocolVersion="1.0"`, and `url=<SOULSEARCHER_PUBLIC_BASE_URL>/api/a2a`. `/api/a2a` uses the existing `/api/*` auth middleware; if `SOULSEARCHER_INTERNAL_API_KEY` is set, A2A clients must send `Authorization: Bearer <key>` or `X-API-Key: <key>`.

Minimal local settings for SoulClaw:

```env
PORT=8001
SOULSEARCHER_PUBLIC_BASE_URL=http://127.0.0.1:8001
SOULSEARCHER_INTERNAL_API_KEY=
SOULSEARCHER_AUTH_USER_HEADER=X-SoulSearcher-User
SOULSEARCHER_A2A_STALLED_TIMEOUT_SECONDS=900
SOULSEARCHER_A2A_IDEMPOTENCY_TTL_SECONDS=86400
SOULSEARCHER_A2A_CALLBACK_RETRY_ATTEMPTS=2
SOULSEARCHER_A2A_CALLBACK_TOKEN=
SOULSEARCHER_A2A_CALLBACK_OUTBOX_ENABLED=true
SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND=temporal
TEMPORAL_ADDRESS=127.0.0.1:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=soulsearcher-deep-research
```

In a SoulClaw integration, SoulSearcher is the DeepResearch Worker and does not explain final results directly to the user:

- A2A task snapshots are persisted through the existing `run_manager`/checkpoint data, so `GetTask`, `CancelTask`, and event inspection still work after restart.
- Temporal is the production long-task execution layer: workflows only orchestrate state, signal, and cancel; LLM, browser, retrieval, and event persistence run in activities so workflows stay deterministic. The `local` backend is only for tests and lightweight development.
- Browser DeepResearch starts with `/api/runs/background` and then subscribes to `/api/runs/{thread_id}/events/sse?live=true`; `/api/research/sse` remains only as a short-session compatibility path.
- Follow-up input with the same `taskId/contextId` resumes an `input-required/auth-required` task via LangGraph `Command(resume=...)`; it does not start a duplicate research run.
- LangGraph interrupts are exposed as structured HITL metadata: `kind`, `thread_id`, `task_id`, `prompts`, `allowed_decisions`, `action_requests`, and `review_configs`.
- Failures use a standard error envelope: `code`, `message`, `stage`, `failure_class`, `retryable`, `ambiguous`, `requires_reconcile`, `trace_id`, `last_event_seq`, `partial_artifacts`, and `suggested_action`.
- `metadata.client_request_id` or `metadata.idempotency_key` provides long-running task idempotency; duplicate start requests with the same payload return the original task, while key reuse with a different payload is rejected.
- Working tasks with no recent events are marked `stalled` and exposed through task events or callback delivery.
- When request metadata includes `callback_url/callback_token`, SoulSearcher writes `task.status_update`, `task.artifact_update`, `task.completed`, `task.failed`, `task.input_required`, and `task.stalled` callbacks to a callback outbox before delivery; with a configured database, callbacks can be retried durably and dead-lettered.

## Development

```bash
# Full local app
./start_soulsearcher.sh

# Backend only
python main.py

# Backend with reload
DEBUG=true SOULSEARCHER_RELOAD=1 python main.py

# Temporal long-task worker
SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND=temporal python -m agent.runtime.temporal_worker

# Full Docker long-task stack
SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND=temporal docker compose -f docker/docker-compose.yml --profile temporal up -d --build
```

```bash
# Python tests
PYTHONPATH=. python -m pytest tests/ -v

# Frontend checks
npx --yes -p node@24 bash -lc 'cd web && node_modules/.bin/next lint'
npx --yes -p node@24 bash -lc 'cd web && node_modules/.bin/tsc --noEmit'

# OpenAPI type sync
DEBUG=false APP_ENV=prod ENABLE_FILE_LOGGING=false \
  python scripts/export_openapi.py --output /tmp/soulsearcher-openapi.json
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/soulsearcher-openapi.json -o web/lib/api-types.ts
npx --yes -p node@24 -p openapi-typescript openapi-typescript /tmp/soulsearcher-openapi.json -o sdk/typescript/src/openapi-types.ts
```

## SDK

Private SDKs live in `sdk/`:

- TypeScript: `SoulSearcherClient`
- Python: `soulsearcher_sdk.SoulSearcherClient`

See [sdk/README.md](sdk/README.md).

## License

MIT License. See [LICENSE](LICENSE).
