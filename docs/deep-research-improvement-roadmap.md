# Weaver Deep Research 改进路线图

本文档基于 `deep-research-comparative-analysis.md`，将三项目对比和前沿调研转化为 Weaver 可落地的工程路线图。

## 路线图原则

### 原则一：先统一语义，再扩展功能

Weaver 已经有很多 Deep Research 相关模块，下一阶段不应先继续堆功能，而应优先统一：

- Research Brief。
- Evidence Schema。
- Quality Gates。
- Artifacts。
- Intermediate Step Events。

这些底层契约统一后，RAG、MCP、多搜索、树状探索、报告导出和评测才能稳定协作。

### 原则二：多智能体只用于研究，不用于并行写作

LangChain Open Deep Research 的经验是：多智能体适合独立子话题研究，但不适合多个 agent 分别写报告章节再拼接。Weaver 应采用：

- supervisor/coordinator 拆分研究。
- sub-agent/branch 负责收集和清洗证据。
- final writer 统一生成最终报告。

### 原则三：质量指标必须驱动控制流

Weaver 已有 query coverage、freshness、citation coverage、ClaimVerifier、benchmark。下一步应让这些指标参与决策：

- 是否继续研究。
- 继续研究哪个 gap。
- 是否切换搜索源。
- 是否补抓页面正文。
- 是否从 linear 升级 tree。
- 是否标注低置信或要求人工确认。

### 原则四：私有知识是一等来源

RAG 和 MCP 不应只是普通工具，而应成为 DeepSearch evidence provider，与 Web search 共享：

- search/fetch 接口。
- evidence id。
- citation id。
- claim verification。
- report/export rendering。

### 原则五：长任务必须产品化

Deep Research 可能运行数分钟到数十分钟。Weaver 应把长任务视为产品基础设施问题，而不是单次 HTTP/SSE 调用问题：

- 后台任务。
- 可恢复。
- 可取消。
- 可重连。
- artifacts 持久化。
- 可通知。

## P0：先做的架构收敛任务

### P0-1：引入 Research Brief

**目标**：在 DeepSearch 前生成一个结构化研究说明，贯穿 query generation、branch decomposition、quality evaluation 和 final writing。

建议字段：

- `original_query`
- `clarified_goal`
- `scope`
- `constraints`
- `expected_fields`
- `preferred_sources`
- `excluded_sources`
- `freshness_requirement`
- `output_format`
- `success_criteria`

参考来源：

- LangChain Open Deep Research 的 Scope / Brief。
- OpenAI Cookbook 的 prompt rewriting 和 fully-formed prompt 要求。

落地方式：

- 在 `deepsearch_node` 或 `run_deepsearch_auto` 前新增 brief generation。
- 对已有 clarify node 进行复用，而不是完全新建一套交互。
- 将 benchmark 中的 `expected_fields` 映射为 `success_criteria`。

验收标准：

- DeepSearch artifacts 中包含 `research_brief`。
- final writer prompt 使用 brief。
- query coverage 能按 brief 的 expected fields 计算，而不是只按原始 query。

风险：

- 额外 LLM 调用增加延迟。
- brief 生成质量不稳定会影响后续流程。

缓解：

- 简单 query 可跳过 brief 或使用规则模板。
- 保存 brief 到 artifacts，便于调试和人工修正。

### P0-2：统一 EvidenceItem Schema

**目标**：用统一证据结构承接 Web、RAG、MCP、fetch、tree finding 和 claim evidence。

建议字段：

- `id`
- `source_type`
- `provider`
- `url`
- `document_id`
- `title`
- `snippet`
- `content_ref`
- `published_date`
- `retrieved_at`
- `query`
- `quality_score`
- `freshness_score`
- `citation_id`
- `metadata`

参考来源：

- OpenAI Deep Research API 的 intermediate steps 和 citation annotations。
- gpt-researcher 的 sources/context 聚合。
- Weaver 现有 `SourceRegistry`、`ClaimVerifier`、`multi_search`。

落地方式：

- 在 `agent/workflows` 下新增 evidence model 模块。
- 为 search result、RAG result、fetched passage 编写 adapter。
- `deepsearch_artifacts` 统一输出 `evidence_items`。
- `ClaimVerifier` 改为优先消费 `EvidenceItem` 或由其转换后的 passages。

验收标准：

- 线性和树状 DeepSearch 都输出同一 evidence schema。
- RAG、Web search、fetch passages 至少三类来源可映射到同一结构。
- citation rendering 不再依赖多套来源格式。

风险：

- 改动面较大，容易影响现有报告引用。

缓解：

- 先做 adapter 层，不删除旧字段。
- 保留 `sources`、`scraped_content` 等兼容字段一个版本周期。

### P0-3：让 Quality Gates 驱动控制流

**目标**：把质量诊断从“报告附加信息”升级为 DeepSearch 的控制信号。

优先接入的 gate：

- query coverage。
- freshness ratio。
- citation coverage。
- unsupported claim count。
- contradicted claim count。
- source diversity。

控制动作：

- 低 query coverage：补充未覆盖维度查询。
- freshness 不足：切换到新闻/实时/新鲜度权重更高的 provider profile。
- citation coverage 不足：补抓页面正文或要求 writer 重写引用。
- unsupported claims 多：回到证据收集或标注低置信。
- contradicted claims > 0：触发 contradiction resolution。

参考来源：

- Agentic RAG survey 的 evaluator-optimizer。
- Weaver 现有 `quality_assessor.py`、`claim_verifier.py` 和 benchmark。

验收标准：

- DeepSearch artifacts 中记录每次 gate 评估和控制动作。
- 至少有一个 gate 能改变下一轮 query 或 provider。
- benchmark 输出能统计 gate-triggered actions。

风险：

- Gate 太严格会导致循环过多。
- Gate 太松又无法提升质量。

缓解：

- 按任务类型设置不同 gate policy。
- 增加 hard budget 和 graceful degradation。

### P0-4：打通报告导出最小闭环

**目标**：把已有 `tools/export/markdown_converter.py` 变成用户可用的 Deep Research artifact。

最小闭环：

- 后端提供导出 API。
- 支持 Markdown、HTML、PDF、DOCX。
- 前端提供下载入口。
- 导出内容包含报告、来源列表、质量摘要。

参考来源：

- gpt-researcher CLI 的报告输出体验。
- Weaver 现有 Markdown converter。

验收标准：

- 任意完成的 DeepSearch 报告可下载 Markdown/HTML。
- 如果依赖可用，可下载 PDF/DOCX。
- 导出报告保留可点击来源链接。

风险：

- PDF/DOCX 依赖可选包，部署环境可能不完整。

缓解：

- Markdown/HTML 为 guaranteed path。
- PDF/DOCX 返回明确依赖缺失错误。

## P1：增强研究能力与产品体验

### P1-1：RAG/MCP 作为一等 Evidence Provider

**目标**：将 `rag_search` 和 MCP search/fetch 接入 DeepSearch 的统一 evidence pipeline。

建议做法：

- 定义 `EvidenceProvider` 接口：`search(query, filters)` 和 `fetch(id)`。
- Web search、RAG、MCP 都实现该接口或 adapter。
- Research Brief 中指定 `source_policy`，例如 web-only、private-first、hybrid。
- 对同一 query 同时检索 Web 和 RAG，再由 reranker/critic 选择证据。

验收标准：

- DeepSearch 可在 `rag_enabled=true` 时自动检索本地文档。
- 报告引用能区分 Web URL 和本地文档 chunk。
- benchmark 至少包含 2 个本地文档/混合来源任务。

### P1-2：Brief-driven Strategy Selector

**目标**：让 DeepSearch 的 `auto` 不只判断简单事实题，还根据 brief 选择策略。

策略类型：

- `linear_light`：简单事实或低成本任务。
- `reflection_loop`：单主题逐步深入，参考 local-deep-researcher。
- `tree`：多维比较、综述、覆盖型任务。
- `supervisor_workers`：多独立子课题，适合并行研究。
- `hybrid_private_web`：私有文档 + Web。

输入信号：

- 任务复杂度。
- expected fields 数量。
- freshness requirement。
- source policy。
- latency/cost budget。
- historical quality metrics。

验收标准：

- `deepsearch_artifacts.strategy_decision` 记录选择理由。
- 至少三类任务能自动选择不同策略。
- 用户可通过 config override。

### P1-3：Span-level Citations

**目标**：将引用从“来源列表/段落编号”升级为可映射到文本 span 的 citation annotations。

参考来源：

- OpenAI Deep Research API 的 `annotations`：title、url、start_index、end_index。

建议做法：

- Writer 输出后，对引用标记和来源建立映射。
- 为每个 citation 保存 `start_index`、`end_index`、`source_id`。
- 前端将引用渲染为可点击、高亮来源。
- 导出 HTML/PDF/DOCX 保持来源链接。

验收标准：

- final report artifacts 中包含 `citation_annotations`。
- 前端能点击引用定位来源。
- citation coverage 指标可基于 annotations 计算。

### P1-4：Research Timeline 与中间步骤可观测

**目标**：标准化 SSE 事件和 artifacts，让用户看到“研究如何完成”。

建议事件类型：

- `brief_created`
- `strategy_selected`
- `query_generated`
- `provider_search_started`
- `provider_search_finished`
- `evidence_selected`
- `quality_gate_evaluated`
- `gap_detected`
- `branch_started`
- `branch_finished`
- `report_written`
- `export_ready`

验收标准：

- 前端能展示时间线。
- benchmark 可统计中间步骤数量和耗时。
- artifacts 可以离线重放关键研究过程。

### P1-5：Deep Research Benchmark 扩展

**目标**：将现有 benchmark 从 smoke signal 扩展为可追踪回归的评测集。

任务类型：

- 时效型：近 30 天信息。
- 多维比较：多个产品/公司/技术路线。
- 科学/学术：需要原始论文和引用。
- 金融/市场：数据、年份和来源可靠性。
- 私有文档：RAG-only 和 hybrid。
- 矛盾证据：需要识别冲突来源。

指标：

- query coverage。
- freshness ratio。
- citation coverage。
- verified/unsupported/contradicted claims。
- source diversity。
- tool calls。
- cost/time。
- repeated URL ratio。
- final report rubric score。

验收标准：

- 至少 20 个 benchmark cases。
- CI 或 nightly 可运行 smoke 集合。
- 每次路线图改动能输出对比报告。

## P2：长期演进方向

### P2-1：Supervisor-Workers 多智能体研究

**目标**：在现有 TreeExplorer 和 coordinator 基础上，演进为 supervisor 动态派发 worker。

约束：

- worker 只负责研究和证据清洗。
- final report 仍由统一 writer 生成。
- supervisor 根据 brief 和 quality gates 判断是否继续。

收益：

- 提升复杂比较任务的覆盖深度。
- 降低单上下文窗口混乱。
- 更适合并行多子话题研究。

### P2-2：交互式继续研究

**目标**：报告生成后允许用户选择某个 section、claim、source 或 gap 继续深入。

能力：

- 对某个 claim 补证据。
- 对某个章节扩写。
- 对某个来源进行反查。
- 对低置信结论继续研究。

### P2-3：领域化研究包

**目标**：针对金融、学术、技术、政策、医疗等领域定义 provider profile、quality policy 和 report template。

示例：

- 学术：优先 arXiv、Semantic Scholar、PubMed、原始论文。
- 金融：优先官方财报、SEC、交易所、权威数据源。
- 技术：优先官方文档、GitHub、RFC、benchmark。
- 时事：优先新闻、官方公告和近 30 天来源。

### P2-4：多模态与数据分析型 Deep Research

**目标**：支持代码执行、表格解析、图表生成和多模态材料。

参考：

- OpenAI Deep Research API 中 code interpreter call 的中间步骤模式。
- Weaver 已有 chart/code/sandbox 相关工具基础。

### P2-5：组织级知识治理

**目标**：面向团队或企业场景，管理私有知识库、MCP server、来源权限和审计日志。

能力：

- per-user/per-tenant RAG collection。
- source access policy。
- evidence audit trail。
- report provenance。
- sensitive source filtering。

## 建议实施顺序

### 第 1 阶段：2 周内完成的收敛基础

优先任务：

1. P0-1 Research Brief。
2. P0-2 EvidenceItem adapter，不立即重构全部调用方。
3. P0-3 Quality Gates 初版，只接入 query coverage 和 citation coverage。
4. P0-4 Markdown/HTML 导出最小闭环。

目标：

- 不大规模重写 DeepSearch。
- 先让 artifacts 结构稳定。
- 先让报告和评测能消费统一结构。

### 第 2 阶段：4 到 6 周完成的能力增强

优先任务：

1. RAG/MCP Evidence Provider。
2. Brief-driven Strategy Selector。
3. Span-level citations。
4. Research Timeline。
5. Benchmark 扩展到 20 个 cases。

目标：

- DeepSearch 从 Web-only 思维切换到 evidence provider 思维。
- 用户能看到过程、来源和质量。
- 改进有可重复评测支撑。

### 第 3 阶段：长期平台能力

优先任务：

1. Supervisor-workers 多智能体研究。
2. 交互式继续研究。
3. 领域化研究包。
4. 数据分析型 Deep Research。
5. 组织级知识治理。

目标：

- 面向复杂生产场景。
- 构建 Weaver 自身差异化能力。
- 从“研究工具”演进为“研究平台”。

## 全局验收标准

### 功能验收

- DeepSearch artifacts 包含 `research_brief`、`strategy_decision`、`evidence_items`、`quality_gates`、`citation_annotations`。
- 线性和树状 DeepSearch 都能输出统一 evidence schema。
- RAG 开启时，DeepSearch 能自动混合检索本地文档和 Web 来源。
- 报告可导出 Markdown/HTML，PDF/DOCX 在依赖可用时可导出。
- 前端能展示关键研究时间线和质量摘要。

### 质量验收

- 默认 benchmark 中 query coverage 不低于当前基线。
- citation coverage 不低于当前基线。
- unsupported/contradicted claim 数量不高于当前基线。
- 复杂比较类任务的 source diversity 有提升。
- time-sensitive 任务 freshness ratio 有提升。

### 性能验收

- 简单事实型问题仍能走低成本路径。
- tree/supervisor 模式有明确工具调用上限。
- 所有策略都受 `deepsearch_max_seconds`、`deepsearch_max_tokens` 或等价 budget 约束。
- benchmark 输出每 case 的耗时、工具调用次数和搜索次数。

### 产品验收

- 用户能理解当前研究处于哪个阶段。
- 用户能看到使用了哪些来源、为什么继续研究、为什么停止。
- 用户能下载报告。
- 引用可点击。
- 低质量或低证据报告有明确提示，而不是假装确定。

## 风险与治理

### 风险一：重构范围过大

如果直接重写 DeepSearch，容易引入回归。建议先通过 adapter 和 artifacts 增量接入，不立即删除旧字段。

### 风险二：质量 gate 导致过度研究

质量 gate 可能让系统为了达到指标不断搜索。必须配合：

- hard budget。
- max tool calls。
- max epochs。
- fallback summary。
- low-confidence disclosure。

### 风险三：引用指标被格式欺骗

只检测引用标记可能无法保证事实真实被来源支持。应同时使用：

- citation coverage。
- claim verifier。
- evidence overlap。
- source diversity。
- 人工抽样评估。

### 风险四：本地 RAG 引入权限问题

私有知识进入 Deep Research 后，需要确保：

- collection 隔离。
- 用户权限检查。
- 来源脱敏。
- artifact 不泄露无权限内容。

### 风险五：多智能体协调复杂

Supervisor-workers 应在 P2 再做，不应过早引入。P0/P1 先让 evidence、brief、quality、artifacts 稳定，再扩展 agent 数量。

## 最终优先级建议

如果只做四件事，建议按以下顺序：

1. **Research Brief**：统一任务目标。
2. **EvidenceItem Schema**：统一证据来源。
3. **Quality Gates Control Loop**：让质量指标真正改变研究行为。
4. **Report Export + Timeline**：把研究结果产品化。

完成这四项后，再推进 RAG/MCP 一等接入、span-level citations 和 supervisor-workers，会更稳妥。
