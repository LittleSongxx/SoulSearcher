# Weaver Research Workspace 二次深度分析与改进建议报告

## 1. 结论摘要

Weaver 已经从最初“综合 Agent 平台雏形”收敛为以 deep research 为核心的 **Research Workspace**。当前主线不再是继续横向扩展聊天、skills、agents、浏览器 viewer、ASR/TTS、triggers/webhooks 等产品面，而是把现有 research SSE、研究会话、证据/引用、质量门禁、报告导出、搜索/RAG、评估与可观测性，打磨成可运行、可验证、可协作、可生产化的研究系统。

本次二次分析新增重点参考项目 **Onyx**。Onyx 对 Weaver 的最大启发不是“功能也很多”，而是它把企业知识源、连接器、索引后台任务、权限过滤、RBAC、引用映射、Deep Research loop、产品化延迟约束和部署治理作为基础设施。Weaver 不应照搬 Onyx 的完整企业平台复杂度，但应吸收其在 **Research Source Layer**、**受约束研究循环**、**Citation Mapping/Evidence Store**、**权限与团队研究空间** 上的设计经验。

联网调研也显示，Deep Research 的竞争重点正在从“能联网搜索”转向“能否稳定产出 citation-rich、可追踪、可评估、可审计的研究报告”。DeepResearch Bench 的 RACE/FACT、OpenAI Deep Research API 的 inline citation metadata 与 intermediate steps、LangChain Open Deep Research 的 Scope → Research → Write、MCP 2025 的 authorization/tasks/security，以及 Anthropic 对 browser prompt injection 的研究，都指向同一结论：Weaver 下一阶段应把 **研究任务规格化、证据链持久化、引用准确性评估、source/RAG 治理、研究过程可观测** 做成一等能力。

## 2. 当前 Weaver Research Workspace 基线

### 2.1 已完成的关键收敛

- **唯一研究流入口**：`POST /api/research/sse`，旧 `/api/research?query` data-stream 与 generic chat SSE 已移除。
- **研究取消控制**：保留 `/api/research/cancel/{thread_id}` 与 `/api/research/cancel-all`。
- **研究会话与证据**：保留 sessions、evidence、continue-research、resume、share、comments、versions 等研究产物能力。
- **证据对象**：`/api/sessions/{thread_id}/evidence` 已能返回 sources、claims、passages、quality gates、timeline、worker_runs、intermediate_steps、citation_annotations 等。
- **前端工作台**：`ResearchWorkspace.tsx` 已成为主入口，配合 `ResearchInput`、`ArtifactsPanel`、`EvidencePanel` 和 SSE-only `researchStreamProtocol.ts`。
- **契约治理**：OpenAPI/TypeScript types/SDK 已随 API 收敛更新，旧 product routes 被契约测试防回归。

### 2.2 当前优势

- **研究链路完整**：输入、SSE 过程、artifact/evidence 展示、quality gap、continue research、export 已形成闭环。
- **证据模型比多数 demo 更强**：claims、passages、quality gates、citation annotations 已具备 Evidence Workspace 的雏形。
- **DeepSearch 能力储备丰富**：`linear`、`tree`、`reflection_loop`、`supervisor_workers` 等策略仍在，且质量诊断、引用修复、claim verifier 等能力较多。
- **工程护栏较强**：OpenAPI drift、测试、SDK types 能防止旧 API 回流。
- **继续研究有差异化**：从 gap/claim/source 生成 follow-up，是 Research Workspace 区别于普通 deep research bot 的关键体验。

### 2.3 当前主要短板

- **领域命名仍有惯性**：前端 hook 仍叫 `useChatStream`，部分研究 UI 仍在 `components/chat`，长期会模糊边界。
- **权威会话源未统一**：前端仍有 localStorage history，后端已有 sessions/version/comment/evidence，两套会话模型容易割裂。
- **Evidence 仍偏运行副产物**：后端从 `deepsearch_artifacts` 读取，而非从持久 Evidence Store 读取。
- **RAG/source routing 不清晰**：documents/search 能力存在，但 Deep Research 尚未把 web/local/private/MCP source 作为明确策略输入。
- **事件协议仍需标准化**：SSE-only 已完成，但 event schema 还应成为稳定 `ResearchRunEvent`。
- **评估尚未形成闭环**：benchmark 和 quality tests 已有，但还未对齐 citation accuracy、effective citations、RACE/FACT-like rubric 与策略对比。

## 3. 原始计划复盘

### 3.1 已失效或不应继续推进的旧方向

原报告部分建议基于“综合 Agent 平台”定位，例如 Agent Workspace、skills/agents 市场、MCP JSON 配置产品面、browser viewer、ASR/TTS、triggers/webhooks 等。当前项目已明确聚焦 Research Workspace，这些方向不应恢复。

- **不建议恢复 Agent Workspace**：除非某些工具能力作为研究内部 runtime，不应作为一级产品入口。
- **不建议恢复 Skills/Agents 公共管理 API**：未来若需要，应重定义为 research template/source policy/eval preset。
- **不建议恢复 MCP config 普通用户 UI**：MCP 应作为受治理的 source/tool preset，而不是任意 JSON 编辑。
- **不建议恢复 browser viewer/debug UI**：网页抓取可作为 research fetcher，但网页内容必须按 untrusted context 处理。

### 3.2 仍然保留且更重要的方向

- **ResearchBrief**：从可选 dict 升级为统一任务规格化对象。
- **ResearchRunEvent**：SSE-only 后更适合建立稳定事件协议。
- **Evidence Store**：把 sources/passages/claims/citation spans/quality gates 持久化。
- **前端 server-state**：sessions/evidence/export/continue-research 不应继续依赖手写 fetch 与 localStorage。
- **Deep Research Eval v1**：从 smoke benchmark 升级为 citation/quality/cost/latency 评估闭环。
- **API/router/service 拆分**：`main.py` 已缩小但仍承担过多职责，仍需领域拆分。

## 4. Onyx 对 Weaver 的关键启发

### 4.1 Onyx 的定位与工程取向

Onyx 是企业 AI/RAG 平台，README 强调 Agentic RAG、Deep Research、Custom Agents、Web Search、Artifacts、Actions & MCP、Code Execution 等，并支持 50+ indexing-based connectors。它的 standard deployment 包含 vector + keyword index、后台 job queue/workers、模型服务、Redis、MinIO 等组件；企业能力覆盖 SSO、RBAC、analytics、audit/query history、custom PII/code handling。

Weaver 不需要照搬完整企业平台，但需要意识到：一旦 Research Workspace 面向真实团队和私有知识源，**连接器、索引任务、权限过滤、审计、后台 worker** 就会从“扩展功能”变成基础设施。

### 4.2 Onyx Deep Research loop

Onyx 的 `backend/onyx/deep_research/dr_loop.py` 提供了一个产品化约束较强的研究 loop：

- **Clarification step**：必要时先询问用户补充上下文。
- **Research plan step**：先生成 research plan，并流式输出 plan start/delta。
- **Research execution step**：orchestrator 使用工具调用驱动研究。
- **Cycle 与时间上限**：普通模型最多 8 cycles，reasoning model 更少；总研究超 30 分钟会强制生成报告。
- **工具白名单**：Deep Research 内只允许 search、web search、open URL 类工具。
- **Think tool**：非 reasoning model 必须通过 think tool 在研究步骤间反思，识别 gaps 并规划下一步。
- **Generate report tool**：研究完成时显式调用 generate_report，或在 cycle/timeout 到达时强制生成。
- **Parallel branches**：多个 research_agent 调用会发出并行分支事件，便于 UI 呈现。
- **Citation mapping**：研究中累积 citation mapping，最终报告生成时传入 citation processor，并保存映射。
- **严格 tool response invariant**：即使 research agent 失败，也生成 synthetic failure response，避免下一轮 LLM 请求违反 provider 协议。

Weaver 当前更像“DeepSearch 策略能力库”，Onyx 更像“受约束的产品 loop”。建议 Weaver 在外层建立统一 `ResearchPipeline` contract：scope → plan → research → reflect → write → verify → persist；不同 DeepSearch 策略只负责 research phase。

### 4.3 Onyx 连接器/RAG/索引层

Onyx 的 connector、credential、connector_credential_pair、index attempt、document index、user/group permission 体系，说明企业 RAG 的核心不是“上传文件后能搜”，而是：

- 知识源对象可管理。
- 索引任务可追踪。
- 文档权限可过滤。
- credential 与 connector 解耦。
- source freshness 与 indexing status 可见。
- 后台任务能处理 docfetching、docprocessing、permissions sync、pruning、KG processing。

Weaver 当前 documents/RAG 能力较轻，建议先建立轻量 **Research Source Layer**：

- `SourceCollection`
- `ResearchConnector`
- `SourceCredential`
- `SourceDocument`
- `SourceIndexAttempt`
- `SourceAccessPolicy`
- `ResearchSourceRoutingPolicy`

短期不必引入 Vespa/Celery 全量复杂度，但要先统一 web/private docs/MCP/internal source 的抽象。

### 4.4 Onyx 权限/RBAC 对团队研究的启发

Onyx 的 `require_permission`、effective permissions、implied permissions，以及 connector_credential_pair 的 user group/curator/admin 过滤，给 Weaver 团队研究空间提供了模型参考。

Weaver 短期可以继续使用 internal key/thread owner，但如果下一步要做协作研究，应为核心对象预留：

- `ResearchSession.owner_id/group_id/visibility`
- `SourceCollection.access_policy`
- `Report.share_policy`
- `Evidence.access_context`
- `RunEvent.auth_context`

不要等 Evidence Store 与 Source Layer 成型后再补权限，否则迁移成本会很高。

### 4.5 Onyx benchmark 的产品约束

Onyx 的 Deep Research Bench submission 明确把产品/UX 约束纳入 benchmark：单问题 research + answer 最多 30 分钟，报告通常约 10,000 tokens，最长可到 20,000 tokens，用户可见输出最大间隔 2 分钟。

Weaver 的评估也应加入产品型指标：

- 首个可见 research event 延迟。
- plan 生成延迟。
- source/evidence 首次出现时间。
- 最大静默时间。
- 总运行时间。
- 报告长度、引用数、质量门禁、成本预算。

## 5. 其他参考项目二次对标

### 5.1 Open Deep Research

LangChain Open Deep Research 采用 Scope → Research → Report Writing：先 clarification 与 brief generation，再由 supervisor 判断是否拆分子主题并派发 sub-agents，最后用 research brief 和 cleaned findings 一次性写最终报告。其经验非常适合 Weaver：

- multi-agent 只用于可并行研究，不用于并行写最终报告。
- sub-agent 输出应是清洗后的 findings，而不是原始网页和工具日志。
- research brief 是研究与写作的 north star。
- context engineering 能降低 token bloat、避免 context window 限制和 TPM rate limit。

### 5.2 GPT Researcher

GPT Researcher 的价值在于研究引擎库化：planner、execution/crawler、source tracking、publisher/report generator 可被 CLI/API/UI 调用。Weaver 可借鉴其 engine/API/UI 解耦和导出能力，但不要退化为简单 report + URLs。Weaver 的差异化应继续放在 evidence graph、quality gates、continue research 和引用可验证性。

### 5.3 DeerFlow

DeerFlow 更像 super agent harness。当前 Weaver 不应恢复泛 Agent Workspace，但可继续借鉴其 gateway/runtime 分层、sub-agent harness、context engineering、生产部署和 E2E 工程治理。

### 5.4 Local Deep Researcher

Local Deep Researcher 的最小闭环是 query generation → web research → summarize → reflection → repeat → final summary，支持 Ollama/LMStudio 和多搜索 API。Weaver 可借鉴它做 **Research Lite Mode**：低成本、本地模型、少 loop、少工具、快速报告，作为开发/离线/低成本模式。

## 6. 联网趋势更新

### 6.1 DeepResearch Bench：评估从文本质量走向引用可信度

DeepResearch Bench 包含 100 个 PhD-level research tasks，覆盖 22 个领域，并设计 RACE 与 FACT 两套评估框架：RACE 评估报告质量，FACT 评估有效引用数量与引用准确性。它还使用人类专家一致性来验证评估框架，说明 deep research 不能只依赖普通 judge prompt。

Weaver 应把 citation coverage、citation accuracy、effective citation count、source freshness、source diversity、unsupported claims、query coverage、instruction following 纳入核心指标。

### 6.2 OpenAI Deep Research API：citation metadata 与 intermediate steps 标准化

OpenAI cookbook 显示，Deep Research API 输出不仅有最终报告，还包含 inline citation annotations：`start_index`、`end_index`、`title`、`url`，并暴露 reasoning steps、web search calls、code execution 等 intermediate steps。它还强调 API 默认不做 clarification，开发者需要 prompt rewriter 或 clarification layer 补齐 scope、metrics、region、source preference、output format。

Weaver 应将 citation span 和 intermediate steps 纳入 Evidence Store 和 ResearchRunEvent，而不是只在 Markdown 中嵌链接。

### 6.3 MCP 2025：工具/数据源标准化必须配套安全治理

MCP security best practices 强调 confused deputy、SSRF、session hijacking、local MCP server compromise、scope minimization 等风险。Tasks 规范强调 task ID 必须绑定 authorization context，否则会暴露任务状态和结果。

Weaver 即使不暴露 MCP config UI，也应为内部 MCP/source/tool 接入建立：server allowlist、tool namespace、risk level、egress policy、OAuth/authorization、task owner binding、audit log、SSRF 防护。

### 6.4 Browser prompt injection：网页内容默认不可信

Anthropic 指出，浏览器 agent 会处理无法完全信任的网页、广告、动态脚本和嵌入文档，而 browser agent 还能点击、填表、下载，风险被放大。即使经过训练、classifier 和红队，prompt injection 仍无法彻底消除。

Weaver 的网页抓取/浏览能力应默认将页面内容标记为 untrusted context，禁止自动高风险动作，并将网页中的指令性文本与事实内容分离处理。

## 7. Weaver 下一阶段路线图

### P0：1-2 周内

1. **ResearchBrief v1**
   - 定义强类型 brief：original query、scope、audience、required dimensions、source preferences、freshness、language、output format、budget、success criteria。
   - `/api/research/sse` 内部先生成/规范化 brief。

2. **ResearchRunEvent v1**
   - 标准化事件：`run.started`、`brief.created`、`plan.delta`、`search.query`、`source.fetched`、`evidence.updated`、`quality.updated`、`report.delta`、`run.completed`、`run.failed`。
   - 前端用 reducer 消费，不再在 hook 内散落状态更新。

3. **Evidence Store skeleton**
   - 最小持久化 sources、passages、claims、citation spans、quality results。
   - evidence endpoint 优先读 store，fallback 到 checkpoint artifacts。

4. **Research Source Routing v1**
   - 把 web/local/hybrid/MCP/internal source 抽象成统一 routing policy。
   - 前端只暴露简单 source mode，不暴露大量 deepsearch knobs。

5. **Research Eval v1 smoke**
   - 建 10 个固定 case，覆盖中文/英文、科技、商业、项目分析。
   - 输出 citation/source/quality/cost/latency summary。

### P1：1-2 个月

1. **ResearchPipeline 阶段化**：scope/plan/research/reflect/write/verify/persist。
2. **SubResearchFinding**：每个 worker 输出 subquestion、findings、citations、confidence、gaps、follow-up queries。
3. **Research Control Panel**：展示 brief、plan、timeline、queries、worker runs、sources、claims、quality gates。
4. **typed API client + server-state**：sessions/evidence/continue/export 使用统一 client，localStorage 只保存 UI preference。
5. **SourceCollection/DocumentCollection**：支持 session-local uploads、global collections、owner/group/access policy。
6. **trace_id/run_id 贯穿链路**：HTTP、SSE、search、fetch、LLM、writer、quality、export 全链路可追踪。

### P2：季度级

1. **团队研究空间**：权限、共享、评论、报告审批、Evidence annotation。
2. **轻量连接器与后台索引**：先支持 GitHub/docs/wiki/web crawl/file upload 等 research-oriented connectors。
3. **MCP governed presets**：管理员配置 preset，普通用户选择受治理的数据源/工具集合。
4. **RACE/FACT-like full eval**：30-100 case，judge prompt/version 管理，策略 leaderboard。

## 8. 可拆 PR 清单

1. **PR 1：ResearchBrief v1**：新增模型、入口规范化、OpenAPI/test 覆盖。
2. **PR 2：ResearchRunEvent v1**：后端 event schema、SSE translate、前端 parser/reducer 测试。
3. **PR 3：Evidence Store Skeleton**：store interface、sources/passages/claims/citation spans 最小写入与读取。
4. **PR 4：Research Source Routing**：web/local/hybrid policy，DeepSearch 接入，前端 source mode。
5. **PR 5：Research Eval v1**：case schema、10 case smoke、markdown/JSON summary。
6. **PR 6：Frontend server-state migration**：typed client，替换 evidence/continue/export/sessions 手写 fetch。
7. **PR 7：Research Control Panel**：brief/plan/timeline/quality/worker runs/citation trace。
8. **PR 8：ResearchPipeline wrapper**：先做外层阶段 contract，再逐步拆 DeepSearch 内部。

## 9. 高风险点与不建议方向

- **不要恢复旧产品面**：generic chat、skills/agents 管理、browser viewer、ASR/TTS、triggers/webhooks 不应回流。
- **不要直接照搬 Onyx 企业栈**：Vespa、多 Celery worker、多租户、SSO 是远期参考，当前先抽象 Source Layer 与 Evidence Store。
- **不要把 deepsearch_config 当产品 API**：复杂 knobs 应变成 preset、source routing、budget policy。
- **不要让 localStorage 成为研究会话权威源**：ResearchSession 应以后端为准。
- **不要只追 benchmark 分数**：还要评估首事件延迟、最大静默时间、报告可读性、成本。
- **不要信任网页内容**：web/MCP/fetch 输出都应视为 untrusted context。
- **不要并行写最终报告**：multi-agent 限制在 research phase，writer 集中生成。

## 10. 最终建议排序

如果下一阶段只能做三件事，建议按以下顺序：

1. **Research data model first**：ResearchBrief + ResearchRunEvent + Evidence Store，把研究任务、过程和证据链变成稳定对象。
2. **Source/RAG integration second**：借鉴 Onyx，把 documents/search/MCP/web source 收敛为 Research Source Layer。
3. **Evaluation loop third**：对齐 DeepResearch Bench 与 OpenAI citation metadata，将 citation accuracy、effective citations、quality gates、latency/cost 纳入持续评估。

完成这三件事后，Weaver 会从“已聚焦的 deep research 应用”升级为“可验证、可协作、可生产化的 Research Workspace”。
