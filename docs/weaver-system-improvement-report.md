# Weaver 全系统深度分析与改进建议报告

## 1. 结论摘要

Weaver 已经不是一个单纯的 Deep Research demo，而是一个覆盖聊天、深度研究、工具调用、浏览器/沙箱、MCP、长期记忆、会话管理、导出、音频、触发器、评估与前端可视化的综合型 Agent 产品雏形。相较四个参考项目，Weaver 的能力面更宽，尤其在工具生态、前端 Research Inspector、OpenAPI 契约、测试覆盖、搜索缓存、多搜索供应商和运行指标上已有明显工程投入。

但当前最大风险也来自“能力面过宽 + 中心单体过重”：`main.py` 约 7993 行，`agent/workflows/deepsearch_optimized.py` 约 5708 行，`agent/workflows/nodes.py` 约 3228 行。大量 API、运行时、流式协议、会话、导出、浏览器、触发器、ASR/TTS、指标和 Agent 控制逻辑集中在少数超大文件中，长期会影响可维护性、测试隔离、权限边界和产品迭代速度。

建议 Weaver 下一阶段不要继续横向堆功能，而应进入“系统化产品化重构”阶段：以 API Gateway/领域路由拆分、Agent Runtime 分层、Deep Research 评测闭环、MCP 安全治理、前端 server-state 化、生产部署加固为主线，把已有能力收束成稳定、可观测、可扩展、可评估的系统。

## 2. 分析依据

### 2.1 Weaver 代码与文档审计范围

- `main.py`：FastAPI 应用、SSE/legacy chat、research、session、share、export、documents、browser、audio、trigger、metrics、MCP、tools、agents 等 API。
- `agent/workflows/deepsearch_optimized.py`：DeepSearch 主实现，包含 tree、linear、reflection_loop、supervisor_workers 等策略。
- `agent/workflows/nodes.py`：路由、clarification、Graph 节点、错误处理。
- `agent/core/smart_router.py`：LLM 路由，输出 `direct`、`agent`、`web`、`deep`、`clarify`。
- `tools/core/registry.py`、`tools/core/mcp.py`：工具注册、发现、MCP 初始化。
- `tools/search/multi_search.py`：多搜索供应商、健康监控、去重、聚合、缓存。
- `tools/sandbox/sandbox_browser_session.py`：浏览器会话与 Playwright 线程隔离。
- `common/config.py`：全局配置、DeepSearch mode 归一化、沙箱/搜索/MCP/Daytona 等配置。
- `common/cancellation.py`：任务取消令牌、检查点、清理回调。
- `common/metrics.py`：内存级 run metrics。
- `common/agents_store.py`：本地 JSON 存储 Agent Profile。
- `web/components/chat/Chat.tsx`、`web/hooks/useChatStream.ts`、`web/components/chat/ArtifactsPanel.tsx`、`EvidencePanel.tsx`、`MetricsDashboard.tsx`、`McpConfigDialog.tsx`：前端核心体验。
- `docs/architecture.md`、`docs/deployment.md`、`docs/openapi-contract.md`、`docs/benchmarks/README.md`：架构、部署、契约、评测文档。
- `.github/workflows/ci.yml`、`benchmark-nightly.yml`：CI、OpenAPI drift guard、frontend build、benchmark nightly。

### 2.2 对标项目

- `deer-flow`：重点参考 API Gateway、LangGraph runtime、auth/checkpointer、middleware、sub-agent harness、skills、sandbox、IM channel、生产部署。
- `gpt-researcher`：重点参考研究引擎库化、CLI/API/UI 多入口、报告导出、source curator、context manager、deep research skill。
- `local-deep-researcher`：重点参考轻量 LangGraph 反思循环、本地模型、JSON/tool-calling fallback、低成本最小闭环。
- `open_deep_research`：重点参考 scope/research/write 三阶段、supervisor/sub-researcher、research brief、context compression、MCP、Deep Research Bench 评估。

### 2.3 联网趋势依据

- DeepResearch Bench：提出 100 个 PhD-level 研究任务，RACE 用于报告质量评估，FACT 用于有效引用和引用准确性评估。
- LangChain Open Deep Research：强调 Scope → Research → Write 三阶段，multi-agent 只用于易并行的研究子任务，最终写作应集中生成以避免报告割裂。
- OpenAI Deep Research API Cookbook：Deep Research API 面向高层查询自动规划、搜索、合成；输出包含 inline citations metadata、中间 reasoning/search/code steps；API 不自动澄清，开发者需要 prompt rewriter 或 clarification 层。
- MCP 2025 规范与生态：MCP 已成为连接工具/数据源的事实标准，强调 tools/resources/prompts、authorization、tasks、streamable HTTP、security best practices、registry 与 enterprise readiness。
- Anthropic browser prompt injection 研究：浏览器 Agent 处理不可信网页时面临隐藏指令、恶意 UI、广告、动态脚本等攻击面，需要分类器、红队、沙箱、权限隔离和持续防御。

## 3. Weaver 当前系统地图

### 3.1 产品能力

Weaver 当前具备以下产品能力：

- **聊天入口**：普通 LLM、web、agent、deep research 多模式。
- **深度研究**：多策略 DeepSearch、搜索/抓取/证据整理、质量门禁、报告生成。
- **Research Inspector**：前端展示 artifacts、claims、sources、passages、quality gaps，并支持继续研究。
- **工具生态**：工具注册表、沙箱、浏览器、文件/文档、搜索、MCP、桌面/视觉/演示/表格等工具。
- **运行控制**：取消、active tasks、中断恢复、run metrics、traces。
- **会话与产物**：sessions、share、comments、versions、export、screenshots、documents。
- **配置与管理**：skills、MCP config、agents profiles、search providers/cache。
- **可观测性**：Prometheus `/metrics`、run metrics dashboard、trace endpoints。
- **部署与治理**：Docker Compose、OpenAPI 类型生成、CI drift guard、secret scan、benchmark nightly。

### 3.2 后端 API 面

`main.py` 中当前至少覆盖这些领域：

- health：`/`、`/health`、`/api/health/agent`
- skills：`/api/skills*`
- chat/research：`/api/chat/sse`、`/api/chat`、`/api/research`、`/api/research/sse`
- cancellation：`/api/chat/cancel/{thread_id}`、`/api/chat/cancel-all`、`/api/tasks/active`
- interrupt：`/api/interrupt/resume`、`/api/interrupt/{thread_id}/status`、`/api/interrupt/{thread_id}/resume`
- MCP/tools/search：`/api/mcp/config`、`/api/tools/registry`、`/api/search/providers`、`/api/search/cache/*`
- agents：`/api/agents*`
- runs/metrics/traces：`/api/runs*`、`/metrics`、`/api/traces*`
- memory：`/api/memory/status`
- browser/screenshots：`/api/browser/*`、`/api/screenshots*`、`/api/events/{thread_id}`
- documents/RAG：`/api/documents/upload`、`/api/documents/list`、`/api/documents/search`、delete
- sessions：`/api/sessions*`、evidence、continue-research、resume、share、comments、versions
- ASR/TTS：`/api/asr/*`、`/api/tts/*`
- triggers/webhook：`/api/triggers*`、`/api/webhook/{trigger_id}`

这说明 Weaver 的产品边界已经接近“Agent 平台”，不是单一研究服务。

## 4. 分层诊断与建议

## 4.1 产品层

### 现状优势

- **能力覆盖宽**：聊天、深度研究、工具调用、浏览器、MCP、RAG、音频、触发器、导出均已接入。
- **Research Inspector 有潜力**：ArtifactsPanel + EvidencePanel 将报告、证据、质量缺口暴露给用户，是 Deep Research 产品差异化关键。
- **Agent Profile 雏形**：`common/agents_store.py` 已有 GPTs-like profile，支持 system prompt、model、enabled tools、MCP override。
- **继续研究闭环**：`/api/sessions/{thread_id}/continue-research` 和前端 `handleContinueResearch` 已形成从 evidence gap 到 follow-up prompt 的闭环。

### 主要问题

- **产品定位过散**：Weaver 同时像 ChatGPT、Deep Research、Manus-like 工具 Agent、浏览器控制台、RAG 文档助手、音频助手、触发器平台。短期展示丰富，但主路径不够聚焦。
- **Deep Research 成果对象还不够产品化**：目前报告、证据、sources、claims、versions、comments、share 已有后端能力，但前端还缺少明确的“研究项目/报告工作台”信息架构。
- **Agent Profile 未形成完整产品闭环**：profile 有存储和 CRUD，但还缺少模板市场、权限、评估、版本、工具安全策略和默认工作流绑定。
- **用户反馈未成为评估数据**：comments/versions/share 是协作基础，但尚未充分转化为质量评估和模型/策略优化数据。

### 建议

#### P0：明确两个一级产品入口

建议前端和 API 层明确区分：

- **Research Workspace**：面向深度研究任务，核心对象是 `ResearchSession` / `Report` / `EvidenceGraph`。
- **Agent Workspace**：面向工具执行和浏览器/沙箱任务，核心对象是 `AgentRun` / `ToolTrace` / `Artifact`。

这样可以避免所有功能都塞进一个 Chat 页面里。

#### P1：把 Evidence Inspector 升级为 Research Control Panel

增加以下产品能力：

- **研究计划**：展示 research brief、子问题、查询计划、当前阶段。
- **证据地图**：claims ↔ passages ↔ sources 的可点击关系。
- **质量门禁**：citation coverage、query coverage、freshness、unsupported claims 用红黄绿展示。
- **继续研究建议**：按 gap、low-confidence claim、stale source 自动生成 follow-up。
- **报告版本对比**：结合已有 versions/comments API，形成研究迭代体验。

#### P1：Agent Profile 产品化

从本地 JSON profile 进化为完整对象：

- profile versioning
- tool allowlist/denylist
- MCP server allowlist
- max budget / max runtime / max parallelism
- default research mode
- evaluation preset
- prompt template variables

#### P2：建立可复用模板与技能市场

参考 deer-flow skills 与 MCP Registry 的方向，形成：

- research templates
- domain-specific skills
- MCP server presets
- safe tool bundles
- benchmark presets

## 4.2 前端层

### 现状优势

- **Next.js + Tailwind + Radix 组件基础完整**。
- **`useChatStream.ts` 支持 SSE 与 legacy 协议**，解析 `status`、`text`、`tool`、`search`、`quality_update`、`research_tree_update`、`sources`、`done` 等事件。
- **Research Inspector、EvidencePanel、MetricsDashboard 已有较强可观察性**。
- **`web/lib/api-types.ts` 由 OpenAPI 生成**，CI 已做 drift guard。

### 主要问题

- **前端状态逻辑集中在 `Chat.tsx` 与 `useChatStream.ts`**：消息、artifacts、threadId、interrupt、status、history、browser、search mode、skill、session 都耦合在一个主流程中。
- **server state 与 local state 混合**：`useChatHistory` 使用 `localStorage` 保存历史，但后端已有 `/api/sessions`、versions、comments、share。两套会话模型容易割裂。
- **API 调用仍手写 fetch**：虽然有 OpenAPI types，但缺少类型安全 API client。`Chat.tsx`、`EvidencePanel.tsx`、`McpConfigDialog.tsx`、`MetricsDashboard.tsx` 都直接拼 URL。
- **ChatInterface 与 Chat 似乎存在新旧实现并存**：`ChatInterface.tsx` 是另一个聊天实现，容易形成重复协议解析和 UI 行为不一致。
- **调试日志遗留**：`Chat.tsx`、`useChatStream.ts` 中存在浏览器 viewer 和 thread header 的 console debug，生产体验应收敛。
- **E2E 为空实现**：`web/package.json` 中 `e2e` 只是打印跳过，关键流式和研究 UI 缺少端到端保障。

### 建议

#### P0：建立 typed API client

基于 `web/lib/api-types.ts` 生成或手写一层轻量 client：

- `api.sessions.getEvidence(threadId)`
- `api.sessions.continueResearch(threadId, payload)`
- `api.mcp.getConfig()` / `api.mcp.updateConfig()`
- `api.runs.list()` / `api.runs.get(threadId)`
- `api.tools.registry()`

前端组件不再直接拼 endpoint，减少 silent drift。

#### P0：统一聊天实现

明确保留 `Chat.tsx + useChatStream.ts` 为唯一主路径，评估是否删除或降级 `ChatInterface.tsx` 为 legacy/demo，避免协议逻辑重复。

#### P1：引入 server-state 管理

参考 deer-flow frontend 的 TanStack Query 方向：

- sessions、reports、evidence、metrics、MCP config、skills、agents profiles 都交给 TanStack Query。
- streaming message state 仍保留本地 reducer。
- localStorage 只保存 UI preference，不保存权威会话内容。

#### P1：将 stream event reducer 独立出来

把 `useChatStream.ts` 中事件解析后的状态更新迁移为纯 reducer：

- 输入：`ChatStreamEvent`
- 输出：`MessageStatePatch`
- 单元测试覆盖所有事件类型
- UI hook 只负责 fetch、abort、dispatch

#### P1：强化 Research UI

- 增加 research timeline。
- 增加 search query list 和 source status。
- 增加 claim verification tooltip。
- 增加 failed/unsupported claim 的一键 follow-up。
- 增加最终报告引用 hover/click source trace。

#### P2：补充 Playwright E2E

至少覆盖：

- SSE chat success
- cancellation
- deep research progress event rendering
- evidence panel load
- continue research
- MCP config validation
- session restore

## 4.3 后端/API 层

### 现状优势

- **FastAPI API 面完整**，OpenAPI contract 文档与 CI 已建立。
- **取消机制较完整**：`common/cancellation.py` 提供 token、checkpoint、cleanup callback、active tasks。
- **内部鉴权、用户隔离、限流已有文档和实现方向**。
- **Prometheus `/metrics` 与 run metrics endpoint 已有基础**。

### 主要问题

- **`main.py` 过大**：约 7993 行，包含太多领域职责，已经超过单文件可维护上限。
- **API 领域边界不清**：skills、chat、research、sessions、documents、browser、audio、triggers、metrics、agents、MCP、tools 都在同一文件。
- **运行时状态大量 in-memory**：run metrics、tool/cache 状态、agents JSON、memory fallback、reports/session 状态等在多 worker 或生产环境可能不一致。
- **认证模型不如 deer-flow 完整**：Weaver 当前更偏 internal key + reverse proxy injection；若要多用户产品化，需要 session/JWT/RBAC/owner filtering 更系统。
- **后台任务与长连接协调复杂**：SSE、legacy streaming、browser WebSocket、event stream、cancellation、interrupt resume 多套协议并存。

### 建议

#### P0：拆分 `main.py` 为领域 router

建议按领域拆出：

- `api/app.py`：FastAPI app factory、middleware、lifespan。
- `api/routers/health.py`
- `api/routers/chat.py`
- `api/routers/research.py`
- `api/routers/sessions.py`
- `api/routers/tools.py`
- `api/routers/mcp.py`
- `api/routers/skills.py`
- `api/routers/agents.py`
- `api/routers/documents.py`
- `api/routers/browser.py`
- `api/routers/audio.py`
- `api/routers/triggers.py`
- `api/routers/metrics.py`

拆分时保持 OpenAPI path 不变，用 contract test 防回归。

#### P0：抽出 Agent Run Service

把 chat/research endpoint 中的运行逻辑抽成服务：

- `RunService.start_chat_run(...)`
- `RunService.start_research_run(...)`
- `RunService.cancel_run(thread_id)`
- `RunService.resume_interrupt(...)`
- `RunService.stream_events(...)`

API router 只做 request/response、auth、错误映射。

#### P1：持久化运行状态

将当前 in-memory 状态逐步迁移：

- run metrics → Redis/PostgreSQL
- cancellation token registry → Redis-backed cooperative cancellation
- agent profiles → PostgreSQL 或至少 versioned JSON store
- session/evidence/report metadata → PostgreSQL
- search cache → Redis

#### P1：借鉴 deer-flow 的 Gateway 模式

deer-flow 的 `app/gateway/app.py` 具备清晰特征：

- FastAPI gateway 与 LangGraph runtime 初始化分离。
- routers 按 domains include。
- AuthMiddleware fail-closed。
- CSRFMiddleware。
- LangGraph auth/checkpointer/store 接入。
- startup 处理 admin/migration。

Weaver 不必完全迁移为 deer-flow，但应借鉴“Gateway 只负责边界，Runtime 负责 Agent”的分层。

#### P2：统一异步任务协议

当前存在 SSE、legacy、browser WS、events endpoint。建议定义统一 `RunEvent` schema：

- `run.started`
- `node.started`
- `node.completed`
- `tool.started`
- `tool.completed`
- `search.query`
- `evidence.updated`
- `quality.updated`
- `artifact.created`
- `interrupt.required`
- `run.cancelled`
- `run.failed`
- `run.completed`

前后端、SDK、测试全部围绕这个 schema。

## 4.4 Agent / Deep Research 层

### 现状优势

- **策略丰富**：`auto`、`tree`、`linear`、`reflection_loop`、`supervisor_workers` 已在配置中支持归一化。
- **ResearchPlanner 已有结构化查询规划**。
- **SmartRouter 已有 direct/agent/web/deep/clarify 路由能力**。
- **DeepSearch 已有缓存、URL 去重、错误恢复、质量诊断、证据提取和 benchmark 测试**。
- **测试覆盖较多**：`tests/test_deepsearch_*`、claim verifier、benchmark、quality diagnostics 等。

### 主要问题

- **DeepSearch 单文件过大**：`deepsearch_optimized.py` 约 5708 行，策略、执行、抓取、质量、报告、事件可能高度耦合。
- **策略分叉过多**：tree、linear、reflection、supervisor_workers 都在同一实现中演化，容易出现行为不一致和测试盲区。
- **缺少统一 Research Brief 层**：虽然有 clarify 和 planner，但与 Open Deep Research 的 Scope → Brief → Research → Write 相比，Weaver 需要更明确的“任务规格化对象”。
- **multi-agent 使用边界需收敛**：Open Deep Research 的经验是 multi-agent 适合并行研究，不适合并行写最终报告；Weaver 应避免多策略并存但无质量路由。
- **评价信号还需产品化**：已有 quality diagnostics，但需要转为可比较、可回归、可监控的指标。

### 建议

#### P0：定义 `ResearchBrief`

新增领域模型，作为 Deep Research 的统一输入：

- original query
- clarified intent
- scope boundaries
- target audience
- output format
- required dimensions
- source preferences
- freshness requirements
- language
- budget constraints
- success criteria

SmartRouter/clarify/planner 都输出或消费 `ResearchBrief`，DeepSearch 不直接面向原始 query。

#### P0：把 DeepSearch 拆成可测试模块

建议拆分：

- `research/brief.py`
- `research/planner.py`
- `research/search_executor.py`
- `research/content_fetcher.py`
- `research/evidence_store.py`
- `research/quality_gates.py`
- `research/strategies/tree.py`
- `research/strategies/linear.py`
- `research/strategies/reflection.py`
- `research/strategies/supervisor_workers.py`
- `research/writer.py`
- `research/events.py`

每个策略共享 evidence/quality/writer，不重复实现。

#### P1：引入策略选择器

基于 query/brief 特征选择策略：

- comparison/list/ranking → supervisor_workers 或 parallel tree
- validation/claim checking → reflection_loop
- narrow factual current info → web/linear
- broad market/science research → tree + supervisor
- low budget/latency → linear + limited queries

策略选择器输出 `strategy`, `budget`, `parallelism`, `quality_gates`，并记录到 run metrics。

#### P1：采用 Open Deep Research 的三阶段

- **Scope**：clarification + brief generation。
- **Research**：supervisor/sub-researcher 或 single strategy，根据 brief 自动选择。
- **Write**：最终报告集中生成，避免并行 section writer 造成割裂。

#### P1：将 sub-agent findings 压缩成结构化结果

参考 Open Deep Research：sub-agent 不返回原始网页/工具日志，而返回：

- subquestion
- findings
- citations
- confidence
- unresolved gaps
- recommended follow-up queries

这能降低 supervisor token bloat。

#### P2：构建 Research Memory

将高质量 research brief、validated sources、domain-specific source preferences、user feedback 存入长期记忆，下一次同领域研究可复用。

## 4.5 工具 / MCP / 沙箱 / 浏览器层

### 现状优势

- **ToolRegistry 设计较完整**：注册、发现、验证、生命周期、metadata、usage stats。
- **MCP 已有 runtime config API 与前端配置 UI**。
- **多搜索供应商成熟度较高**：Tavily、Bocha、DuckDuckGo、Brave、Serper、Exa 等；包含健康监控、失败统计、质量分、URL canonicalization。
- **浏览器会话考虑了 Playwright 线程亲和性**。
- **工具能力面广**：浏览器、沙箱、视觉、演示、表格、图像、文档等。

### 主要问题

- **工具安全策略不足以支撑开放产品**：工具 allowlist、risk level、approval policy、sandbox policy、network policy 需要更明确。
- **MCP 配置目前更像开发者功能**：前端可直接编辑 JSON，如果进入多用户/生产环境，需要权限、审计、校验和隔离。
- **浏览器 Agent prompt injection 风险高**：Anthropic 明确指出网页、邮件、广告、动态脚本都可能注入恶意指令，浏览器工具必须默认不可信。
- **工具调用观测与成本归因还不够细**：ToolRegistry 有 usage stats，但需要按 run/user/profile 维度沉淀。

### 建议

#### P0：为工具定义风险等级

为每个工具增加：

- `risk_level`: `read_only` / `external_request` / `filesystem_write` / `code_execution` / `browser_action` / `credential_access`
- `requires_approval`
- `allowed_in_profile`
- `sandbox_required`
- `network_policy`
- `max_runtime_ms`
- `audit_payload_policy`

#### P0：MCP server 安全治理

参考 MCP security best practices 和 2025 spec：

- server schema validation
- tool name namespace
- remote MCP authorization
- token audience binding
- SSRF 防护
- local server install allowlist
- per-user MCP credential isolation
- MCP tool call audit log
- MCP server health check

#### P1：浏览器工具默认隔离

针对 browser use：

- 默认无凭据浏览。
- 不自动读取敏感站点。
- 页面内容作为 untrusted context 标注。
- 高风险动作必须 HITL approval。
- 下载/上传/表单提交必须弹确认。
- 对网页内容做 prompt injection classifier 或规则扫描。
- 浏览器运行在 sandbox / remote browser 中，避免访问本机敏感文件。

#### P1：工具调用统一审计

记录：

- run_id
- user_id/profile_id
- tool name/version
- args hash / redacted args
- start/end/error
- latency
- output size
- risk level
- approval state
- cost estimate

#### P2：构建 Tool Marketplace / MCP Presets

将稳定工具和 MCP server 模板化，配合风险等级和 profile policy 使用。

## 4.6 数据 / 记忆 / RAG / 证据层

### 现状优势

- **Mem0 集成 + local JSON fallback**。
- **documents upload/list/search API 已有 RAG 雏形**。
- **EvidencePanel 支持 sources、claims、passages、quality gates**。
- **claim verifier 和 citation artifacts 有测试覆盖**。
- **OpenAPI 契约保证前后端类型一致**。

### 主要问题

- **数据权威源不统一**：localStorage history、backend sessions、JSON agents、fallback memory、Redis/PostgreSQL/FileStore 并存。
- **证据模型还需标准化**：claims、sources、passages、quality_gates 已有，但应形成明确 schema 和存储层。
- **RAG 与 Deep Research 未完全融合**：documents search 是接口能力，但 Deep Research 策略应能显式使用 private docs / MCP data / web source。
- **长期记忆缺少隐私与用户隔离策略**。

### 建议

#### P0：定义核心数据模型

建议明确这些核心对象：

- `User`
- `AgentProfile`
- `ResearchSession`
- `Run`
- `RunEvent`
- `Artifact`
- `EvidenceSource`
- `EvidencePassage`
- `Claim`
- `QualityGateResult`
- `DocumentCollection`
- `MemoryEntry`
- `MCPServerConfig`

#### P1：建立 Evidence Store

证据不应只作为运行时副产物，应持久化：

- sources table
- passages table
- claims table
- claim_source_links
- quality_gate_results
- report_citation_spans

前端 EvidencePanel 从 Evidence Store 读取。

#### P1：RAG/source routing

ResearchBrief 中增加 source routing：

- web only
- local docs only
- hybrid
- MCP internal data
- domain whitelist
- domain blacklist
- source freshness requirement

#### P2：记忆治理

- user-level memory namespace
- project-level memory namespace
- opt-in memory save
- sensitive data redaction
- memory delete/export
- memory retrieval audit

## 4.7 运维 / 部署 / 安全层

### 现状优势

- **Docker Compose 包含 PostgreSQL pgvector、Redis、backend、frontend**。
- **deployment 文档覆盖 Docker、Vercel、Railway/Render、reverse proxy internal auth、rate limit、SSE 注意事项**。
- **CI 包含 backend test、frontend build、docker build、secret scan、OpenAPI drift guard**。
- **Prometheus metrics endpoint 已存在**。

### 主要问题

- **Compose 中 backend command 使用 `--reload`**：`docker/docker-compose.yml` 设置 `APP_ENV=production`，但 command 仍带 `--reload`，这更像开发模式，不适合作为生产 compose 默认。
- **缺少 nginx/reverse proxy 一体化示例**：deer-flow production compose 包含 nginx/front/gateway/sandbox provisioner，而 Weaver 目前 Compose 更偏本地全栈。
- **多 worker 下 in-memory 状态不安全**：run metrics、cancellation、cache、active tasks 在多进程会分裂。
- **浏览器/沙箱安全边界需要生产级说明**：尤其是文件系统、网络、凭据、下载、MCP local server。
- **认证仍偏内网/反代假设**：若 Weaver 作为团队产品，需要完整身份系统。

### 建议

#### P0：拆分 dev compose 与 prod compose

- `docker-compose.dev.yml`：保留 bind mount、reload、frontend dev server。
- `docker-compose.prod.yml`：关闭 reload，使用构建产物，添加 nginx，配置 SSE buffering off，healthcheck，资源限制。

#### P0：生产安全 checklist

新增 `docs/security.md`：

- internal API key
- reverse proxy auth
- user isolation
- rate limit
- CORS allowlist
- MCP allowlist
- sandbox network policy
- browser action approval
- secret scan
- prompt injection mitigation
- logs redaction

#### P1：OpenTelemetry / LangSmith tracing

Prometheus metrics 只能回答“多少/多快/是否错”，不能回答“Agent 为什么这样做”。建议：

- OpenTelemetry trace_id/run_id 贯穿 HTTP、SSE、tool、LLM、search。
- 可选 LangSmith tracing 用于 graph/LLM 调试。
- 前端 MetricsDashboard 支持 trace deep link。

#### P1：运行预算和成本控制

- per-run token budget
- per-tool budget
- per-search provider budget
- max parallelism
- max browser duration
- max report length
- profile-level quota

#### P2：团队/多用户化

借鉴 deer-flow AuthMiddleware：

- fail-closed auth
- cookie/JWT session
- RBAC
- owner filtering
- CSRF
- admin setup/migration

## 4.8 评估 / Benchmark 层

### 现状优势

- **已有 `docs/benchmarks/README.md` 和 `scripts/benchmark_deep_research.py`**。
- **CI 有 workflow_dispatch benchmark smoke，nightly 有 10-case benchmark artifact**。
- **测试集中覆盖 DeepSearch budget、quality、multi-search、claim verifier、golden smoke 等**。

### 主要问题

- **benchmark 规模和指标仍偏内部 smoke**，还未对齐 DeepResearch Bench 的 RACE/FACT 思路。
- **缺少回归门槛**：benchmark artifact 上传了，但 PR 是否因为质量下降而失败还不明确。
- **用户反馈没有进入评估数据集**。
- **不同策略之间缺少量化对比**：tree vs linear vs reflection vs supervisor_workers 的成本/质量/延迟权衡需要数据。

### 建议

#### P0：建立 Weaver Research Eval v1

每个 benchmark case 包含：

- query
- expected dimensions
- preferred source types
- disallowed source types
- freshness requirement
- minimum citation count
- required sections
- reference answer outline
- grading rubric

指标：

- citation coverage
- citation accuracy sample
- source diversity
- source freshness
- query coverage
- unsupported claim count
- report structure score
- instruction following
- latency
- token/search cost

#### P1：参考 DeepResearch Bench 的 RACE/FACT

- **RACE-like**：基于 reference + adaptive criteria 评估报告质量。
- **FACT-like**：有效引用数、引用准确率、来源可信度。
- 用较强 judge LLM 做离线评估，并保存 judge prompt/version。

#### P1：策略对比 dashboard

按相同 case 跑：

- `linear`
- `tree`
- `reflection_loop`
- `supervisor_workers`
- `auto`

输出：质量、成本、耗时、失败率、citation metrics。

#### P2：从用户反馈生成 eval case

当用户点击“继续研究”、修改报告、评论某段“不准确”时，生成候选 eval case，形成真实任务闭环。

## 4.9 工程治理层

### 现状优势

- **Ruff、pytest、secret scan、compileall、frontend build、OpenAPI drift guard 都已接入 CI**。
- **测试数量较多**：当前 `tests` 下约 319 个 Python 测试文件。
- **OpenAPI contract 文档清晰**。
- **Makefile 提供统一命令**。

### 主要问题

- **长文件与复杂度债务明显**。
- **部分前端测试/E2E 还未落地**。
- **缺少 ADR（Architecture Decision Record）**：DeepSearch 多策略、MCP、安全、数据存储等重大设计需要记录取舍。
- **没有明确模块 owner 和边界规则**。
- **文档覆盖面已有，但可能与实现漂移**。

### 建议

#### P0：设置复杂度治理红线

- 新增文件不超过 800 行，超过必须拆模块。
- 单函数不超过 120 行，超过需解释或拆分。
- `main.py`、`deepsearch_optimized.py`、`nodes.py` 设置重构 milestone。
- CI 增加可选复杂度报告，不一定立即 fail。

#### P1：建立 ADR

建议新增：

- `docs/adr/0001-api-router-split.md`
- `docs/adr/0002-research-brief.md`
- `docs/adr/0003-run-event-schema.md`
- `docs/adr/0004-tool-risk-policy.md`
- `docs/adr/0005-evidence-store.md`
- `docs/adr/0006-deepsearch-strategy-selection.md`

#### P1：前端测试补齐

- stream parser unit tests 已有基础则扩大覆盖。
- reducer tests。
- API client tests。
- Playwright E2E。

#### P2：SDK 产品化

既然已有 `sdk/typescript`，建议：

- 发布 internal npm package。
- 提供 typed client。
- 提供 stream event parser。
- 提供 examples。

## 5. 四个参考项目对 Weaver 的启发

## 5.1 deer-flow

### 可借鉴点

- **Gateway 分层**：`app/gateway/app.py` 清晰包含 app factory、lifespan、middleware、router include。
- **强认证**：AuthMiddleware fail-closed，cookie/JWT 校验，internal auth，request.state.user 和 contextvar owner filtering。
- **CSRF 与多用户治理**：适合团队/生产产品。
- **LangGraph runtime 标准化**：`langgraph.json` 配置 graph、auth、checkpointer。
- **checkpointer 后端抽象**：memory/sqlite/postgres，async context manager 管理生命周期。
- **sub-agent harness**：lead agent、middleware、subagent 并发限制、todo、memory、loop detection、clarification。
- **生产 Compose**：nginx + frontend + gateway + sandbox provisioner。
- **前端工程成熟**：TanStack Query、LangGraph SDK、Vitest、Playwright。

### 不宜直接迁移点

- deer-flow 更像完整平台，复杂度高。Weaver 不应一次性照搬所有 auth/channel/sandbox runtime，而应优先拆 `main.py` 和 runtime boundary。

### Weaver 应采取的动作

- P0 借鉴 router/gateway 拆分。
- P1 借鉴 auth/checkpointer/store。
- P1 借鉴 frontend server-state 与 E2E。
- P2 借鉴 channel/skill marketplace。

## 5.2 gpt-researcher

### 可借鉴点

- **研究引擎库化**：`GPTResearcher` 类可被 CLI、API、UI 调用。
- **报告导出明确**：Markdown、PDF、DOCX。
- **报告类型丰富**：basic、detailed、deep research、multi_agents。
- **ContextManager / ReportGenerator / ResearchConductor 分工清晰**。
- **MCP 作为 retriever 扩展接入**。
- **轻量 ReportStore**：适合本地 demo 快速落地。

### 不宜直接迁移点

- API 服务安全、持久化、生产治理较轻，不适合作为 Weaver 的生产架构模板。
- WebSocket manager 和 app 也有一定单体特征。

### Weaver 应采取的动作

- 将 Deep Research 引擎库化，API 只是调用者。
- 强化导出格式和报告对象。
- 保留 Weaver 更强的 evidence/quality 体系，不退化成简单 report store。

## 5.3 local-deep-researcher

### 可借鉴点

- **最小 LangGraph 闭环清晰**：query generation → web research → summarization → reflection → finalize。
- **本地模型友好**：Ollama、LMStudio、JSON/tool-calling fallback。
- **配置简单**：max loops、search API、fetch full page、tool calling。
- **适合作为 Weaver lite mode**。

### 不宜直接迁移点

- 产品、API、前端、持久化、评估都较轻，不适合完整 Weaver。

### Weaver 应采取的动作

- 提供 `local/lite research mode`：低成本、本地模型、少工具、少依赖。
- 将 JSON/tool-calling fallback 标准化，增强兼容本地模型。

## 5.4 open_deep_research

### 可借鉴点

- **Scope → Research → Write** 三阶段非常适合 Weaver。
- **research brief** 作为研究成功的 north star。
- **supervisor/sub-researcher** 只用于研究阶段，最终报告集中写。
- **sub-agent context isolation** 解决多主题查询中的 context clash。
- **sub-agent findings compression** 减少 token bloat。
- **MCP/native search/search API 可配置**。
- **评估脚本** 包含 pairwise、supervisor parallel evaluation。

### 不宜直接迁移点

- open_deep_research 是研究 agent skeleton，不覆盖 Weaver 的完整产品/工具/前端/部署。

### Weaver 应采取的动作

- P0 引入 ResearchBrief。
- P1 重构 DeepSearch 为三阶段。
- P1 明确 multi-agent 只用于可并行研究。
- P1 将 sub-agent 输出结构化压缩。

## 6. 横向对比矩阵

| 维度 | Weaver | deer-flow | gpt-researcher | local-deep-researcher | open_deep_research | 建议方向 |
|---|---|---|---|---|---|---|
| 产品定位 | 综合 Agent 平台雏形 | Agent 平台/工作台 | 研究引擎 + UI/CLI | 本地轻量研究 | 深度研究框架 | Weaver 聚焦 Research + Agent 两个 workspace |
| 后端结构 | FastAPI 大单体 | Gateway + routers + runtime | FastAPI 服务较轻 | LangGraph dev | LangGraph graph | 拆 `main.py` 为领域 router |
| Auth | internal key/反代注入 | fail-closed JWT/RBAC/CSRF | 较轻 | 无 | LangGraph auth | P1 建多用户 auth |
| Deep Research | 多策略、能力丰富 | subagent harness 可参考 | breadth/depth 递归 | reflection loop | supervisor/sub-researcher | 统一 ResearchBrief + strategy selector |
| Multi-agent | 已有 supervisor_workers | lead/subagent 成熟 | multi_agents 可选 | 无 | 核心范式 | 只用于并行研究，不并行最终写作 |
| Context engineering | 有摘要/质量逻辑 | middleware/summarization | context manager | summarizer/reflection | brief + sub-agent compression | 强化结构化压缩 |
| Evidence/citation | EvidencePanel/claim verifier | artifacts | source URLs | 简单来源 | citations | 对齐 RACE/FACT |
| MCP | 后端 + 前端配置 | gateway mcp | retriever 扩展 | 无 | 可配置 MCP | 增加安全治理和 presets |
| 前端 | Inspector 强，但状态集中 | TanStack Query/LangGraph SDK/E2E | UI package | 无 | LangGraph Studio | server-state + typed client |
| 部署 | compose dev/prod 混合 | nginx/front/gateway/provisioner | compose 简单 | Docker dev | LangGraph dev | 拆 dev/prod compose |
| 评估 | benchmark 已有 | 测试多 | 测试一般 | 少 | evaluation scripts | 建 Weaver Research Eval |
| 工程治理 | CI/OpenAPI 强，长文件债 | 领域分层强 | 引擎复用强 | 简洁 | 框架清晰 | 复杂度治理 + ADR |

## 7. 行业趋势与 Weaver 应对

### 7.1 Deep Research 正从“搜索+总结”走向“可评估研究系统”

DeepResearch Bench 强调 100 个专家构造任务、RACE 报告质量、FACT 引用有效性和准确性。这说明未来 Deep Research 竞争点不是“能不能联网”，而是：

- 能否覆盖复杂研究维度。
- 引用是否准确。
- 有效引用数量是否足够。
- 报告是否符合指令。
- 质量是否可重复评估。

Weaver 应将 citation coverage、citation accuracy、unsupported claims、query coverage、freshness、source diversity 作为核心指标。

### 7.2 Multi-agent 的正确使用边界变清晰

Open Deep Research 和 Anthropic/LangChain 经验都指向：

- multi-agent 适合子主题相互独立的研究。
- multi-agent 不适合让多个 agent 并行写最终报告。
- supervisor 的价值在于动态决定深度和并行度。
- sub-agent 应压缩 findings，而不是返回原始工具日志。

Weaver 应收敛 `supervisor_workers` 策略，把它定位为研究阶段加速与 context isolation，而不是通用多 Agent 万能解。

### 7.3 Context Engineering 成为 Agent 核心工程

深度研究 token 重，multi-agent token 更重。行业实践强调：

- chat history → research brief
- raw tool output → cleaned findings
- long pages → citation passages
- supervisor context → compressed evidence

Weaver 的 DeepSearch 重构应优先围绕 context pipeline，而不是继续增加搜索轮数。

### 7.4 MCP 正成为工具/数据源标准，但安全治理同步变重要

MCP 2025 生态强调 authorization、tasks、streamable HTTP、security best practices、registry、enterprise readiness。Weaver 已接入 MCP，是优势；但要产品化必须补：

- MCP server allowlist
- OAuth/authorization
- tool-level risk policy
- audit log
- SSRF 防护
- local server install security
- per-user credential isolation

### 7.5 浏览器 Agent 安全是高风险领域

Anthropic 对 browser prompt injection 的分析表明：所有网页内容都可能是不可信指令源。Weaver 有浏览器/Playwright/截图/stream 能力，因此必须默认：

- 页面内容不可信。
- 浏览器动作需要最小权限。
- 敏感动作需要人类确认。
- 沙箱隔离优先。
- 日志与下载要审计。

### 7.6 商业 API 正提供中间步骤和 citations metadata

OpenAI Deep Research API 暴露 final report、inline citation annotations、reasoning/search/code intermediate steps。这对 Weaver 的启发：

- stream event schema 要标准化。
- 引用 span 要结构化，而不只是 Markdown 链接。
- 中间步骤要能用于调试、可视化、评估。
- clarification/prompt rewriting 是 API 使用者责任，Weaver 应内置这一层。

## 8. 优先级路线图

## 8.1 P0：1-2 周内建议完成

### P0-1：拆分 API Router 骨架

- 新增 `api/routers/*`。
- 先迁移 health、skills、mcp、tools、search、agents、runs 等低耦合 endpoints。
- 保持 path 不变。
- 跑 OpenAPI drift guard。

### P0-2：建立 ResearchBrief 模型

- 定义 Pydantic model。
- clarify/planner/deepsearch 输入输出逐步接入。
- 在 run metrics 中记录 brief summary。

### P0-3：建立 typed frontend API client

- 基于 `web/lib/api-types.ts`。
- 先替换 EvidencePanel、McpConfigDialog、MetricsDashboard、continue-research。

### P0-4：定义 Tool Risk Policy v1

- 给 ToolMetadata 增加风险字段或外部 policy map。
- 高风险工具默认需要 approval。
- MCP 工具默认标记 external/untrusted。

### P0-5：拆分 dev/prod compose

- dev 保留 reload。
- prod 关闭 reload，增加 nginx 示例。
- 更新部署文档。

## 8.2 P1：1-2 个月内建议完成

### P1-1：DeepSearch 模块化重构

按 planner/search/fetch/evidence/quality/writer/strategies 拆分。

### P1-2：统一 RunEvent Schema

- 后端所有 stream 输出同 schema。
- 前端用 reducer 消费。
- SDK 提供 parser。

### P1-3：Evidence Store 持久化

- PostgreSQL schema。
- evidence API 从 store 读。
- report citation spans 可追踪。

### P1-4：Weaver Research Eval v1

- 30-50 个本地 case。
- RACE/FACT-like 指标。
- nightly benchmark 输出趋势。

### P1-5：前端 Research Workspace

- 将聊天页中的研究产物拆成工作台。
- Evidence graph、timeline、quality gates、version compare。

### P1-6：OpenTelemetry/LangSmith 可观测性

- run_id/trace_id 贯穿 HTTP、tool、LLM、search。
- 前端可跳转 trace。

## 8.3 P2：季度级能力建设

### P2-1：多用户与权限系统

- JWT/cookie session。
- RBAC。
- owner filtering。
- CSRF。
- admin setup。

### P2-2：Agent Profile 市场化

- profile templates。
- skill bundles。
- MCP presets。
- eval presets。

### P2-3：团队协作研究

- share/comment/version 已有基础，继续做 collaborative review。
- report approval workflow。
- source annotation。

### P2-4：成本智能调度

- 模型路由。
- 预算感知策略。
- search provider cost/quality routing。
- cache reuse。

## 9. 可拆 PR 清单

### PR 1：API router split foundation

- 创建 `api/app.py`、`api/routers/health.py`、`api/routers/mcp.py`。
- 从 `main.py` 迁移低风险 endpoints。
- 保持 OpenAPI 不变。
- 验证 `pytest`、OpenAPI drift guard。

### PR 2：Frontend typed API client

- 新增 `web/lib/api-client.ts`。
- 替换 `EvidencePanel`、`McpConfigDialog`、`MetricsDashboard` 中手写 fetch。
- 添加单元测试。

### PR 3：ResearchBrief v1

- 新增 `agent/research/brief.py`。
- clarify/planner 输出 brief。
- DeepSearch 接受 brief 但保留兼容 query。

### PR 4：RunEvent schema v1

- 新增后端 `RunEvent` Pydantic model。
- 新增前端 TS type。
- `useChatStream` 改为 reducer 消费。

### PR 5：Tool Risk Policy v1

- 扩展 tool metadata。
- 为现有工具配置默认 risk。
- 高风险工具在 UI 显示 badge。

### PR 6：DeepResearch Eval v1

- 新增 eval case schema。
- benchmark 输出 citation/source/quality/cost 指标。
- nightly artifact 增加 markdown summary。

### PR 7：Dev/Prod compose split

- `docker-compose.dev.yml`
- `docker-compose.prod.yml`
- nginx config 示例。
- 更新 `docs/deployment.md`。

## 10. 高风险点提醒

- **不要继续把新 API 加进 `main.py`**：任何新功能都应优先进入 router/service。
- **不要让多 Agent 写最终报告**：会产生风格和结构割裂。
- **不要把 MCP JSON 编辑暴露给普通用户**：生产环境应使用 presets + admin 权限。
- **不要依赖 in-memory run state 支撑多 worker**：上线前必须迁移关键运行状态。
- **不要把 localStorage 当权威会话存储**：前端历史应与后端 sessions 对齐。
- **不要默认信任网页内容**：浏览器 Agent 必须按不可信输入处理。

## 11. 最终建议排序

如果只能做三件事，建议按以下顺序：

1. **系统解耦**：拆 `main.py` + `DeepSearch`，建立 API router / RunService / ResearchBrief。
2. **质量闭环**：把 evidence、citation、quality gates、benchmark 做成可回归指标。
3. **产品聚焦**：把 Research Inspector 升级为 Research Workspace，使 Weaver 的差异化从“功能多”转向“研究过程透明、证据可验证、结果可迭代”。

完成这三件事后，Weaver 会从一个能力丰富但复杂度快速上升的 Agent 项目，升级为一个架构清晰、可评估、可生产化、可扩展的 Deep Research / Agent 平台。
