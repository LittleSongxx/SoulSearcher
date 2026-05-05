# Weaver Deep Research 质量提升调研与改造报告

本文基于本次 `supervisor_workers` 10-case bilingual benchmark 结果、Weaver 当前实现审计、相关论文/基准与开源项目调研，给出提升 Deep Research 报告质量、引用可信度和声明支撑率的改造建议。

## 1. 结论摘要

本次测试证明 Weaver 的 `supervisor_workers` 策略在**稳定完成**上表现良好，但在“可被证据逐条支撑的高可信研究报告”上仍有明显提升空间。

核心判断：

- **稳定性已达标**：10/10 completed，0 failed，0 timeout，`stable_completion_rate=1.0`。
- **报告质量未达标**：`rubric_pass_rate=0.6`，4 个 case 未通过 report rubric。
- **主要短板不是报告长度或来源数量**：每个 case 报告约 8.5k-16.2k chars，来源 17-25 个，evidence_items 111-121 个。
- **主要短板是证据绑定粒度**：所有重点 case 的 `passages=0`，说明系统虽收集到很多来源，但没有形成可靠的 passage-level evidence notebook。
- **引用准确率表面较好但有效来源偏少**：`citation_accuracy=0.89`，但 `avg_effective_citations=2.3`，说明正文引用往往集中在少量来源，不能充分支撑复杂多维报告。
- **声明支撑问题最严重**：`unsupported_claim_rate=0.36`，10/10 claim judge 均未通过当前 `<=0.1` 阈值。

因此，下一阶段优先级应从“多搜、多写”转向“**先建立声明-证据账本，再按账本写作和修订**”。

## 2. 本次 benchmark 结果诊断

### 2.1 Summary 指标

| 指标 | 结果 | 解读 |
| --- | ---: | --- |
| total_cases | 10 | 5 English + 5 Chinese |
| completed_cases | 10 | supervisor_workers 无执行失败 |
| stable_completion_rate | 1.0 | 产出长度和来源存在性稳定 |
| rubric_pass_rate | 0.6 | 只有 6/10 报告通过综合 rubric |
| citation_accuracy | 0.89 | 抽样引用大多能被来源支持 |
| unsupported_claim_rate | 0.36 | 声明未支撑率偏高 |
| avg_effective_citations | 2.3 | 有效独立来源数量偏少 |
| mean_duration_s | 213.192 | 当前质量下耗时可接受 |

### 2.2 Report judge 逐项问题

未通过 report rubric 的 case：

| Case | Score | 主要问题 |
| --- | ---: | --- |
| `web_dr_001` | 7.0 | `evidence_quality=6`，企业级 coding assistant 对比中证据支撑不足 |
| `web_dr_005` | 7.0 | `evidence_quality=5`，向量数据库对比存在 unsupported operational/pricing claims |
| `web_dr_007` | 5.0 | coverage/depth 不足，缺失“地区政策差异、企业买方指标、企业披露” |
| `web_dr_009` | 7.0 | `evidence_quality=6`，医疗/长期安全性等高风险声明支撑不足 |

通过但仍有 claim 问题的 case：

- `web_dr_004`：citation accuracy 只有 `0.5`，说明量子纠错技术结果引用存在错配。
- `web_dr_010`：report score 9，但 claim unsupported rate `0.6`，说明报告整体结构好，但部分矿产/产业链推断没有足够证据绑定。

### 2.3 证据层面的关键发现

从 `cases/*/evidence.json` 抽查：

- `sources`：17-25 个。
- `evidence_items`：111-121 个。
- `citation_annotations`：部分 case 超过 100 个。
- `passages`：全部为 0。
- `claims`：有 deterministic claim verifier 输出，但它只作为 artifact/quality_summary 记录，没有驱动补搜或重写。

这说明当前系统是“source/snippet-backed writing”，还不是“passage-grounded writing”。对 Deep Research 来说，后者才是降低 unsupported claims 的关键。

## 3. 外部调研结论

### 3.1 DeepResearch Bench：质量评估应同时看报告和引用事实性

DeepResearch Bench 将 Deep Research Agent 定义为能进行多步网页探索、定向检索和高阶综合，并产出 citation-rich reports 的系统。它采用两类互补评估：

- **RACE**：reference-based adaptive criteria-driven evaluation，用于评估报告质量。
- **FACT**：评估 effective citation count 和 citation accuracy，用于衡量信息收集与引用可信度。

对 Weaver 的启发：

- 不能只看完成率和报告长度。
- 应把 `effective citation count`、`citation accuracy`、`rubric pass rate` 作为核心回归指标。
- 当前 `avg_effective_citations=2.3` 偏低，应作为 P0/P1 优化目标。

参考：

- [DeepResearch Bench](https://deepresearch-bench.github.io/)
- [DeepResearch Bench paper](https://arxiv.org/abs/2506.11763)

### 3.2 DRACO：真实用户场景中，事实准确性与引用质量比格式更重要

DRACO 来自 Perplexity 的生产任务抽样，覆盖 10 个领域。它用专家 rubric 评估四个维度：

- 事实准确性
- 广度与深度
- 展示质量
- 主来源引用质量

其中事实准确性约占一半标准，rubric 中也包含对 hallucinations 和 unsupported claims 的负向惩罚。

对 Weaver 的启发：

- 当前 `unsupported_claim_rate=0.36` 是比 Markdown 结构更高优先级的问题。
- 评估和优化应按 domain/language 分桶，而不是只看 overall summary。
- 对医疗、法律、金融、政策等任务，应默认提高引用/声明校验阈值。

参考：

- [DRACO benchmark article](https://research.perplexity.ai/articles/evaluating-deep-research-performance-in-the-wild-with-the-draco-benchmark)
- [DRACO paper](https://arxiv.org/abs/2602.11685)

### 3.3 ALCE：引用质量应拆成 citation recall 和 citation precision

ALCE 对引用文本生成提出两个关键指标：

- **citation recall**：每个 statement 是否至少有一个引用 passage 能 entail 它。
- **citation precision**：引用是否相关，避免无关引用滥用。

对 Weaver 的启发：

- 现在的 citation judge 抽样评估只能给整体 `citation_accuracy`。
- 产品侧应把每个事实句或关键 claim 拆出来，绑定到 passage，再检查 entailment。
- citation 不应只是 report 末尾的自动参考来源，也不应只依赖 `[n]` 位置映射。

参考：

- [ALCE paper](https://ar5iv.labs.arxiv.org/html/2305.14627)
- [ALCE GitHub](https://github.com/princeton-nlp/ALCE)

### 3.4 RARR：生成后必须有 research-and-revise loop

RARR 的核心流程是：

1. 对生成文本生成覆盖所有待验证方面的问题。
2. 为每个问题检索 evidence snippets。
3. 用 agreement model 判断文本与 evidence 是否一致。
4. 只在不一致时最小化编辑原文。

对 Weaver 的启发：

- 现有 `claim_verifier` 只记录 unsupported/contradicted，不驱动修订。
- 应增加 final verification revise loop：检测 unsupported claims 后，要么补搜，要么删除/降级该声明，要么明确说明资料不足。
- 对报告整体质量提升，post-generation revise loop 比单纯加强 writer prompt 更可靠。

参考：

- [RARR paper](https://ar5iv.labs.arxiv.org/html/2210.08726)

### 3.5 Self-RAG：检索、生成、批判应由显式状态控制

Self-RAG 的思想是让模型在生成过程中判断是否需要检索、内容是否被支持、答案是否完整。

对 Weaver 的启发：

- 当前 `quality_gates` 在 supervisor round 中偏粗粒度，主要看 query coverage / freshness。
- 应把“需要补搜 / 证据不足 / 声明未支撑 / 引用不足”变成 supervisor decision 的显式状态，而不是最终 artifact。
- writer 阶段也需要 critique/rewrite，而不是一次性 final report。

参考：

- [Self-RAG paper](https://arxiv.org/abs/2310.11511)
- [Self-RAG project](https://selfrag.github.io/)

### 3.6 STORM：pre-writing 阶段决定最终报告质量

STORM 将长文生成拆为：

- pre-writing：收集 references，生成 outline。
- writing：基于 outline 和 references 写完整文章。

它强调 perspective-guided question asking 和模拟对话式 follow-up questions，并指出 long-form grounded article 的主要错误常来自 red herrings，即看似相关但实际牵强的来源或内容。

对 Weaver 的启发：

- `build_worker_tasks()` 当前 focus 过于通用：expected_fields + evidence/risks/implementation/freshness。
- 应先生成 report outline / evidence requirements，再派 worker。
- source triage 应在写作前完成，避免把低相关来源混入 final synthesis。

参考：

- [STORM project](https://storm-project.stanford.edu/research/storm/)
- [STORM GitHub](https://github.com/stanford-oval/storm)

### 3.7 FreshQA / FreshPrompt：时效任务需要日期、来源、片段显式进入 prompt

FreshPrompt 将 search evidence 规范化为 source/date/title/snippet/highlight，并引导模型基于最新证据推理。

对 Weaver 的启发：

- 对 `freshness_requirement=recent/current` 的 benchmark task，应显式要求 worker 搜索近期来源。
- writer prompt 中应要求区分“截至日期”“来源发布时间”“事实发生时间”。
- 对中国政策、医疗、供应链等任务，应该优先官方/监管/论文/公司披露的一手来源。

参考：

- [FreshLLMs / FreshQA paper](https://ar5iv.labs.arxiv.org/html/2310.03214)

### 3.8 BrowseComp / ReAct / FRAMES：复杂研究需要持久搜索、交替推理与多跳约束

BrowseComp 强调持久浏览和创造性搜索；ReAct 强调 reasoning/action 交替；FRAMES 强调 factuality、retrieval、reasoning 三者统一评估。

对 Weaver 的启发：

- supervisor 不应在“有足够搜索结果”时过早 synthesize。
- 对多维任务，应维护每个 expected dimension 的 retrieval/coverage 状态。
- 对 numerical、temporal、multi-constraint claims，应有专门 verification worker 或 rubric-aware checker。

参考：

- [BrowseComp paper](https://arxiv.org/abs/2504.12516)
- [ReAct article](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)
- [FRAMES paper](https://arxiv.org/html/2409.12941v3)

### 3.9 开源项目对比

#### gpt-researcher

本地代码显示它具备以下值得借鉴的结构：

- `ResearchConductor.plan_research()`：先基于初始搜索生成 sub-queries。
- `SourceCurator.curate_sources()`：用 LLM 评估来源相关性、可信度和可靠性。
- `ReportGenerator`：分阶段生成 report/introduction/conclusion/subtopics。
- 成本追踪：按 step 记录 cost，便于做质量/成本 tradeoff。
- MCP tool selector：为不同研究任务选择合适工具。

可借鉴点：

- 给 Weaver 增加 source curator / source triage worker。
- 将 final report writer 拆成 outline、section writing、conclusion、revision。
- 对每个阶段记录 token/cost/质量指标，便于 benchmark 分析。

#### local-deep-researcher

它的 LangGraph 工作流非常轻量：

`generate_query -> web_research -> summarize_sources -> reflect_on_summary -> conditional loop -> finalize_summary`

可借鉴点：

- 在 Weaver supervisor round 之后增加 reflection gap query，而不是只依赖 static missing_topics。
- 每轮 summary 后让模型明确输出 knowledge_gap 和 follow_up_query。
- 这个模式成本低，适合作为 P0/P1 的补搜机制。

#### Open Deep Research

Open Deep Research 将模型分为 Summarization、Research、Compression、Final Report，并支持多 search tools / MCP。

可借鉴点：

- Weaver 也应显式拆分 summarization / research / compression / final report / verification model。
- 将 compression 作为证据压缩阶段，而不是让 final writer 直接读大量 summary_notes。

参考：

- [Open Deep Research GitHub](https://github.com/langchain-ai/open_deep_research)

## 4. Weaver 当前实现差距

### 4.1 任务分解：worker focus 不够 rubric-aware

当前 `agent/workflows/supervisor_workers.py::build_worker_tasks()` 使用：

- `missing_topics`
- `brief.expected_fields`
- `evidence`
- `risks`
- `implementation`
- `freshness`

问题：

- 对 benchmark task 的 `judge_rubric`、`source_constraints`、`freshness_requirement` 利用不足。
- worker 没有明确角色，如 source triage、policy timeline、pricing/license checker、clinical evidence checker。
- 对 `web_dr_007` 这种多地区/企业披露/买方指标任务，worker 容易覆盖泛化维度而漏掉细分维度。

### 4.2 Supervisor 决策：过早 synthesize

当前 `decide_supervisor_next_step()` 只要：

- 有搜索结果
- 没有 query_coverage failed gates

就可能 `synthesize`。

问题：

- 没有检查 claim support。
- 没有检查 citation coverage。
- 没有检查每个 expected dimension 是否至少有一手/可信来源。
- final 阶段的 quality gates 不会反向触发补搜或重写。

### 4.3 Evidence pipeline：有 evidence_items，但缺少 passage notebook

当前本次 run：

- evidence_items 很多。
- passages 全部为 0。
- fetched_pages 为空。

虽然 `deepsearch_optimized.py` 已有 `_fetch_pages_and_passages()`，`evidence_passages.py` 也可 split passages，但 supervisor_workers 路径没有实际启用页面抓取/片段抽取。

影响：

- writer 只能看到 search result snippet 级别信息。
- claim verifier 也主要依赖 snippet overlap。
- citation judge 抽样能过，但无法保证所有关键 factual claims 都被 passage 支撑。

### 4.4 Writer prompt：有引用要求，但没有 claim ledger

`prompts/templates/deepsearch/final_summary.py` 已要求：

- facts/data/time/comparison/recommendations 句末加 `[n]`。
- `[n]` 必须来自可引用来源。
- 缺少来源时说明资料不足。

但缺少：

- 写作前生成“声明-证据表”。
- 每节只使用通过 source triage 的 evidence。
- 高风险声明必须绑定 quote/passage。
- 写作后进行 unsupported claim rewrite。

### 4.5 Claim verifier：目前偏 deterministic overlap，不能作为强事实校验

`claim_verifier.py` 通过 token overlap 匹配 claim 与 evidence。优点是快、稳定、低成本；缺点是：

- 中文长句和同义改写召回不稳。
- 不能准确判断复杂因果、比较、政策影响、长期安全性。
- 对数值、时间线、实体错配敏感度不足。
- 结果只进入 `quality_summary`，不驱动修订。

### 4.6 Benchmark summary：缺少诊断维度

当前 `summary.md` 只展示 overall 指标，缺少：

- judge coverage：30/30 是否 scored。
- 按 language/domain 的 pass rate。
- report dimensions 平均分。
- 每 case unsupported/contradicted claims。
- effective citations 分布。
- evidence_items/passages/fetched_pages 计数。

这会降低后续优化可观察性。

## 5. 改造 Backlog

## P0：优先解决 unsupported claims 和 evidence_quality

### P0-1. 引入 claim ledger：写作前先生成“声明-证据账本”

目标文件：

- `agent/workflows/deepsearch_optimized.py`
- 新增可选模块：`agent/workflows/claim_ledger.py`
- `prompts/templates/deepsearch/final_summary.py`

核心改动：

- 在 final report 前，从 `summary_notes + sources + evidence_items` 中生成结构化 ledger：
  - `claim`
  - `required_dimension`
  - `evidence_ids`
  - `source_urls`
  - `quote/snippet`
  - `support_status`
  - `confidence`
- writer prompt 改成基于 ledger 写作，而不是只读 summary_notes。
- 明确要求没有 ledger 支撑的事实不得写成确定性结论。

预期收益：

- 降低 `unsupported_claim_rate`。
- 提升 `evidence_quality`。
- 提升 `avg_effective_citations`。

### P0-2. supervisor_workers 启用 passage-level evidence

目标文件：

- `agent/workflows/deepsearch_optimized.py`
- `agent/workflows/evidence.py`
- `agent/workflows/evidence_passages.py`

核心改动：

- 在 supervisor_workers final synthesis 前，对 top sources 或 top evidence urls 调用 `_fetch_pages_and_passages()`。
- 将 `fetched_pages` 和 `passages` 纳入 `build_evidence_items()`。
- claim verifier 优先使用 passages，而不是只用 search snippets。

注意：

- 只抓取 top N 来源，避免延迟暴涨。
- 对官方文档/论文/监管来源提高优先级。

预期收益：

- `passages` 从 0 提升到可用数量。
- claim verifier 可返回 passage/quote 级证据。
- citation judge 更容易验证关键声明。

### P0-3. final verifier revise loop

目标文件：

- `agent/workflows/deepsearch_optimized.py`
- `agent/workflows/claim_verifier.py`
- 新增 prompt：`prompts/templates/deepsearch/revise_report.py`

核心改动：

- final_report 生成后运行 claim verifier。
- 如果 unsupported/contradicted 超阈值：
  - 对 unsupported claim 生成 targeted follow-up query；或
  - 修改报告，删除/弱化/标注资料不足；或
  - 为 claim 补充已有 evidence 引用。
- 最多 1-2 轮，避免无限循环。

预期收益：

- 直接对齐 RARR 的 research-and-revise 思路。
- 把当前 quality artifacts 变成质量控制闭环。

### P0-4. quality gates 驱动 continue/rewrite，而不是只记录

目标文件：

- `agent/workflows/quality_gates.py`
- `agent/workflows/supervisor_workers.py`
- `agent/workflows/deepsearch_optimized.py`

核心改动：

- 在 final stage 将 `claim_verifier_unsupported`、`citation_coverage_score` 接入 supervisor decision。
- 当 citation/claim gate fail 时，action 不应是单纯结束，而应是：
  - `add_gap_queries`
  - `improve_citations`
  - `resolve_claims`
  - `revise_report`

预期收益：

- 避免 `web_dr_010` 这种 report score 高但 claim unsupported 高的情况进入最终结果。

### P0-5. Benchmark summary 增加诊断指标

目标文件：

- `eval/deep_research_benchmark/metrics.py`
- `eval/deep_research_benchmark/reports.py`

新增指标：

- judge scored coverage：report/citation/claim 各自 scored 数。
- avg report dimensions：coverage/depth/evidence_quality/freshness/overall。
- per-language pass rate。
- per-domain pass rate。
- avg evidence_items / passages / sources。
- avg claim total/supported/unsupported/contradicted。
- citation effective sources p50/p90。

预期收益：

- 让后续每次优化都能定位是检索、证据、写作还是校验问题。

## P1：提升检索覆盖、来源质量和多维综合能力

### P1-1. Rubric-aware worker roles

目标文件：

- `agent/workflows/supervisor_workers.py`
- `agent/workflows/research_brief.py`

核心改动：

- worker task 不只用 generic focus，还要根据：
  - `expected_dimensions`
  - `judge_rubric`
  - `source_constraints`
  - `freshness_requirement`
  生成角色。
- 示例角色：
  - `official_source_worker`
  - `benchmark_comparison_worker`
  - `pricing_license_worker`
  - `policy_timeline_worker`
  - `risk_counterargument_worker`
  - `claim_verification_worker`

预期收益：

- 提升 coverage/depth。
- 避免 `web_dr_007` 这类 expected dimensions 漏项。

### P1-2. Source curator / source reliability scoring

目标文件：

- 新增：`agent/workflows/source_curator.py`
- `agent/workflows/evidence.py`
- `agent/workflows/evidence_providers.py`

核心改动：

- 类似 `gpt-researcher` 的 `SourceCurator`，对来源打分：
  - relevance
  - authority
  - freshness
  - primary_source
  - independence
  - language/domain fit
- final writer 只优先使用 curated top sources。
- 对医疗/法律/金融/政策任务，一手来源权重更高。

预期收益：

- 降低 red herrings。
- 提升 evidence_quality。
- 提升 citation precision。

### P1-3. Reflection gap query loop

目标文件：

- `agent/workflows/supervisor_workers.py`
- 新增：`agent/workflows/research_reflection.py`

核心改动：

- 借鉴 local-deep-researcher，在每轮 summary 后输出：
  - `knowledge_gap`
  - `follow_up_query`
- 用 gap queries 驱动下一轮 worker，而不是只依赖 query coverage heuristic。

预期收益：

- 提升复杂任务的持久搜索能力。
- 对 BrowseComp/FRAMES 类多跳任务更友好。

### P1-4. 多语言 query expansion 与 source routing

目标文件：

- `agent/workflows/query_strategy.py`
- `agent/workflows/domain_router.py`

核心改动：

- 中文任务：生成中文 + 英文 query，尤其是科技/医疗/气候/供应链等全球资料领域。
- 英文任务：必要时补充中文 query，用于中国政策/供应链/厂商信息。
- 根据 domain 选择 provider_profile。

预期收益：

- 提升跨语言覆盖和 source diversity。
- 减少单语言检索偏差。

### P1-5. Structured report schema

目标文件：

- `prompts/templates/deepsearch/final_summary.py`
- 新增：`agent/workflows/report_schema.py`

核心改动：

- final report 前先生成 outline，每个 expected dimension 对应至少一个 section。
- 每节包括：
  - key findings
  - supporting evidence
  - caveats / uncertainty
  - citations
- 对比较任务强制表格。
- 对政策/医疗任务强制 timeline / risk-benefit。

预期收益：

- 提升 coverage、structure、depth。
- 降低漏项。

## P2：长期架构升级

### P2-1. Evidence notebook

构建可持久化 evidence notebook，包含：

- sources
- passages
- claims
- citations
- source reliability scores
- contradictions
- freshness metadata
- dimension coverage map

让 writer、verifier、frontend evidence panel、benchmark judge 共用同一证据结构。

### P2-2. Multi-agent verification worker

在 supervisor_workers 中加入专门 verification worker：

- claim verifier worker
- citation auditor worker
- source contradiction worker
- missing dimension worker

该 worker 不负责写新内容，只负责指出必须补搜或改写的 claims。

### P2-3. Benchmark dashboard

为 `eval/deep_research_benchmark` 增加 dashboard/export：

- per-case radar chart
- report dimensions heatmap
- citation/claim table
- domain/language breakdown
- before/after run comparison

### P2-4. Multi-benchmark regression suite

逐步引入：

- 当前 10-case bilingual benchmark。
- DeepResearch Bench 风格任务。
- DRACO 风格 weighted rubric。
- BrowseComp 风格 needle/persistent browsing tasks。
- FreshQA 风格 time-sensitive factuality tasks。
- ALCE 风格 citation recall/precision tests。

## 6. 建议的实施顺序

### 第一阶段：1-2 个 PR

优先做：

1. Benchmark summary 增加诊断指标。
2. supervisor_workers 启用 passage-level evidence。
3. final writer prompt 加 claim ledger 输入格式。
4. final verifier revise loop 先做 1 轮。

目标：

- `unsupported_claim_rate` 从 0.36 降到 <= 0.20。
- `avg_effective_citations` 从 2.3 提升到 >= 4.0。
- `rubric_pass_rate` 从 0.6 提升到 >= 0.75。

### 第二阶段：2-4 个 PR

继续做：

1. Rubric-aware worker roles。
2. Source curator。
3. Reflection gap query loop。
4. 多语言 query expansion。

目标：

- `rubric_pass_rate` >= 0.8。
- `citation_accuracy` >= 0.9。
- `unsupported_claim_rate` <= 0.15。
- `avg_effective_citations` >= 5。

### 第三阶段：架构化升级

长期做：

1. Evidence notebook。
2. Verification worker。
3. Benchmark dashboard。
4. 多基准回归。

## 7. 复测方案

固定条件：

- run directory 新建，不覆盖当前结果。
- judge model 固定 `deepseek-v4-pro`。
- dataset 仍使用当前 10-case bilingual tasks。
- strategy 仍使用 `supervisor_workers`。
- timeout 仍为 900s。

A/B 对照：

- Baseline：当前 `run_supervisor_10_bilingual_20260504_181428`。
- Variant A：P0 passage + claim ledger + revise loop。
- Variant B：Variant A + source curator + rubric-aware worker。

必须记录：

- total/completed/timeout。
- p50/p90/mean duration。
- report dimensions。
- citation accuracy。
- unsupported claim rate。
- effective citations。
- evidence_items/passages/sources。
- per-language/per-domain 指标。

成功标准：

- 不牺牲完成率：completed_cases 仍为 10/10。
- 延迟可控：mean duration 不超过 baseline 1.5x。
- 质量改善：rubric pass、unsupported claims、effective citations 至少 2/3 明显改善。

## 8. 文件级改造索引

| 文件 | 建议改动 |
| --- | --- |
| `agent/workflows/deepsearch_optimized.py` | supervisor final 前启用 passage fetch；加入 claim ledger；加入 final verifier revise loop |
| `agent/workflows/supervisor_workers.py` | rubric-aware worker roles；质量 gate fail 后继续补搜/修订 |
| `agent/workflows/quality_gates.py` | citation/claim gate 从记录变为控制信号 |
| `agent/workflows/claim_verifier.py` | 支持 passage-first verification；增强中文/数值/时间线 claims 处理 |
| `agent/workflows/evidence.py` | evidence item 增加 reliability/dimension/claim refs |
| `agent/workflows/evidence_providers.py` | source reliability 与 provider failover metadata |
| `prompts/templates/deepsearch/final_summary.py` | 从 citation-only prompt 升级为 claim-ledger grounded writing prompt |
| `eval/deep_research_benchmark/metrics.py` | 增加诊断指标和 judge coverage |
| `eval/deep_research_benchmark/reports.py` | summary.md 输出 per-case/per-dimension/per-language 细分 |

## 9. 最推荐的下一步

我建议下一步先实现 P0 中的最小闭环：

1. `passages` 接入 supervisor_workers。
2. final report 前构建 claim ledger。
3. final report 后运行 verifier，失败则 1 轮 rewrite。
4. benchmark summary 增加诊断指标。

这组改动最直接针对本次结果中的根因：**来源足够但证据粒度不足、引用存在但有效来源偏少、声明生成后未被证据闭环约束**。
