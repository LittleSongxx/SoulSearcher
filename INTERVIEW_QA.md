# Weaver 项目深度面试 Q&A 集

> 由面试官视角对 Weaver (LangGraph Deep Research Agent) 项目的全面拷打。
> 涵盖架构设计、多智能体编排、上下文工程、状态管理、模型路由、记忆系统、质量保障、MCP/Skills 集成及通用八股文考察。

---

## 目录

1. [项目架构与 Deep Research Pipeline](#1-项目架构与-deep-research-pipeline)
2. [多智能体编排 (Multi-Agent Orchestration)](#2-多智能体编排-multi-agent-orchestration)
3. [上下文工程 (Context Engineering)](#3-上下文工程-context-engineering)
4. [状态管理与 LangGraph](#4-状态管理与-langgraph)
5. [模型路由与成本优化](#5-模型路由与成本优化)
6. [记忆系统 (Memory System)](#6-记忆系统-memory-system)
7. [质量保障与评估体系](#7-质量保障与评估体系)
8. [MCP、Skills 与工具系统](#8-mcp、skills-与工具系统)
9. [Agent 通用八股文](#9-agent-通用八股文)
10. [系统设计与场景追问](#10-系统设计与场景追问)

---

## 1. 项目架构与 Deep Research Pipeline

### Q1：你们整个 Deep Research 的 Pipeline 是怎么设计的？从用户输入到最终报告经历了哪些阶段？

**A**：整个 Pipeline 是一个 **LangGraph StateGraph**，分为五个阶段：

1. **Input Gateway（输入网关）**：clarify → research_brief → classify_complexity。先判断是否需要澄清问题，然后生成结构化研究简报，最后根据复杂度分流——**simple 走 direct_answer 快路径，standard/deep 进入完整 Orchestrator-Workers 模式**。

2. **Research Plan（HITL 计划门）**：使用 Google Gemini 模式——先规划，再通过 **LangGraph interrupt 暂停等用户 approve/revise/cancel**，通过后才执行昂贵的 supervisor。

3. **Supervisor Subgraph（监督者循环）**：Orchestrator 根据研究简报，通过 ConductResearch tool **并行 spawn N 个 Researcher 子图**，每个 Researcher 是独立的 ReAct 循环。Supervisor 通过 ThinkTool 结构化反思（gaps/confidence/strategy），自主决定何时 ResearchComplete。

4. **Researcher Subgraph（研究者 ReAct 循环）**：每个 Researcher 有搜索工具 + think_tool + ResearchComplete，在 **search → reflect → search → reflect** 循环中收集信息，最后走 **三层混合压缩** 输出压缩结果。

5. **Report Generation（报告生成）**：源策展 → 报告撰写 → **Level 1 即时质量检查 + 自动修订**（最多2次）→ Level 3 深度评估（仅 deep 复杂度）→ 记忆异步更新。

**关键设计决策**：**子图嵌套**（Supervisor 和 Researcher 都是独立编译的 StateGraph），边界清晰，每个子图有自己的 typed state。

---

### Q2：为什么选择 LangGraph 而不是 LangChain 的 AgentExecutor，或者 CrewAI/AutoGen 这些框架？

**A**：

- **LangGraph vs LangChain AgentExecutor**：AgentExecutor 是一个黑盒循环，你无法精确控制每一步的状态流转。LangGraph **把控制权交给开发者**——每个节点是纯函数，状态通过 TypedDict 显式传递，图的拓扑完全可控。对于 Deep Research 这种需要子图嵌套、条件路由、HITL 的场景，LangGraph 是更合适的选择。

- **LangGraph vs CrewAI**：CrewAI 适合角色化多 Agent 的快速原型，但它的任务分配和结果汇总比较粗粒度。Weaver 需要 **Orchestrator 自主决定何时 spawn 多少 Researcher、每个 Researcher 的 depth/breadth**，这需要精确的图控制而非角色扮演。

- **LangGraph vs AutoGen**：AutoGen 偏向对话式多 Agent 协作，研究型友好但生产部署的确定性不如 LangGraph 的状态机模型。

- **Anthropic 的建议**：**不要过早引入 Multi-Agent**。一个强大的单 Agent + 工具往往比多个简单 Agent 协作更稳定。Weaver 只在 "需要并行研究不同子主题" 时才 spawn 多个 Researcher，这符合 Orchestrator-Workers 模式而非松散的 Multi-Agent。

---

### Q3：项目中多处提到 "集成 open_deep_research、gpt-researcher、deer-flow 三个项目的精华"，具体哪些设计来自哪个项目？

**A**：

| 来源 | 借鉴的设计 | 在 Weaver 中的位置 |
|------|-----------|-------------------|
| **open_deep_research** | supervisor ⇄ supervisor_tools 子图模式、override_reducer、compress_research、GAIA 评估、token-limit 重试 | supervisor.py, state.py, researcher.py, report.py |
| **gpt-researcher** | 三层混合压缩 (raw→embedding→LLM)、三模型路由 (fast/smart/strategic)、ThinkTool 结构化反思、SourceCurator | researcher.py, configuration.py, supervisor.py, report.py |
| **deer-flow** | 结构化输出模型 (Pydantic as LangChain tools)、ToolErrorHandler、LoopDetector、TokenUsageTracker、MemoryMiddleware、view_image/extract_web_images 多模态支持、SKILL.md 技能系统 | state.py, middleware.py, multimodal.py, tools/, skills/ |

核心创新点在于 **把三者的设计融合到一个统一的 LangGraph StateGraph 中**，而非简单堆砌。

---

## 2. 多智能体编排 (Multi-Agent Orchestration)

### Q4：你是如何实现 Supervisor 动态决定 spawn 多少个 Researcher 的？不是预先写死 3 个或 5 个？

**A**：关键设计在于 **ConductResearch 被定义为 LangChain 的 bind_tools 工具**，不是一个预定义的节点。

Supervisor 的 LLM 每次决策时可以调用 0 到 N 个 ConductResearch，每个指定不同的 topic、context、thoroughness。代码在 `supervisor_tools()` 中收集所有 ConductResearch tool_call，通过 **`asyncio.gather` 并行执行**：

```python
research_tasks = [
    _get_researcher_subgraph().ainvoke({...}, config)
    for tc in allowed_calls
]
tool_results = await asyncio.gather(*research_tasks)
```

**受限于 `max_concurrent_research_units`（默认5）**，超出的 call 会收到 overflow 提示，要求下个迭代重试。

**ThinkTool 是收敛关键**：Supervisor 不是固定次数循环，而是通过结构化反思（`gaps_identified`、`confidence_level`、`next_strategy`）自主决定何时 `ResearchComplete`。这避免了固定迭代次数的浪费。

---

### Q5：Supervisor 和 Researcher 之间的通信协议是怎样的？Researcher 返回什么给 Supervisor？

**A**：通信通过 **LangGraph 子图状态** 实现：

- Supervisor 调用 ConductResearch 时，为每个 Researcher 构建初始 `ResearcherState`（researcher_messages、research_topic、thoroughness）
- Researcher 子图完成后，通过 `ResearcherOutputState` 返回：
  - `compressed_research`：经过三层混合压缩后的结构化研究结果
  - `raw_notes`：原始工具调用记录（供最终报告参考）
- Supervisor 收到的是 **ToolMessage**，content 就是 compressed_research

**关键设计**：Supervisor 收到的不是原始搜索结果（可能几万字），而是经过压缩的摘要。这遵循了 **Claude Code 的 sub-agent 原则："子代理在自己的上下文中完成工作，只返回摘要"**。

---

### Q6：为什么选择 Orchestrator-Workers 模式而不是完全去中心化的 Multi-Agent？Anthropic 的建议怎么落地的？

**A**：Anthropic 在 "Building Effective Agents" 中强调：

1. **简单优先**：能用单 Agent + 工具解决的不要上多 Agent
2. **Orchestrator-Workers 适用场景**：任务可以分解为独立子任务，子任务间不需要复杂协商
3. **去中心化 Multi-Agent 的风险**：调试困难、token 消耗爆炸、收敛不可控

Weaver 的落地方式：

- **Simple 复杂度**：直接跳过 Supervisor，单 Agent (direct_answer) 完成 → 零编排开销
- **Standard/Deep 复杂度**：Orchestrator-Workers，Supervisor 集中决策，Researcher 独立执行
- **没有引入 Agent-to-Agent 协商**：Researcher 之间不通信，所有结果汇总到 Supervisor
- **每个 Researcher 是独立子图**：有自己完整的状态、工具、循环，但都受 Supervisor 控制

---

## 3. 上下文工程 (Context Engineering)

### Q7：你们的"三层混合压缩"具体是怎么工作的？每层的触发条件和 fallback 策略是什么？

**A**：三层混合压缩位于 `researcher.py` 的 `compress_research()` 节点：

**第一层：Raw Pass-through（<8K chars）**
- 触发条件：聚合内容 < 8000 字符
- 策略：**零成本，直接返回原始内容**
- 适用：简单的 fact-check 或搜索结果很少的情况

**第二层：Embedding Similarity Filter（8K-50K chars）**
- 触发条件：内容在 8K-50K 字符之间
- 策略：使用 `text-embedding-3-small` 对内容切片做 embedding，用 `EmbeddingsFilter` 按 `similarity_threshold`（默认 0.35）过滤与研究主题相关的 chunks
- **代价低但效果中等**：不需要 LLM 调用，但可能丢失上下文关联
- **Fallback**：如果 embedding 库不可用或失败，降级到第三层 LLM 压缩

**第三层：LLM Semantic Compression（>50K chars）**
- 触发条件：内容 > 50K 字符或 embedding 压缩失败
- 策略：使用 smart_llm 做语义压缩，**保留所有关键信息并添加引用标记**
- **最多3次重试**：每次失败后截断消息（移除旧消息以减少上下文大小）
- **代价最高但质量最好**：LLM 理解语义后进行信息浓缩

---

### Q8：你们是怎么做上下文预算控制的？Supervisor 的上下文不会随着迭代无限膨胀吗？

**A**：上下文预算控制在两个层面：

**1. ToolMessage 层面**：`_enforce_context_budget()` 函数
- **每个 ConductResearch 的 ToolMessage 截断到 8K 字符**（compression_small_threshold）
- Researcher 已经返回压缩结果，这是安全网

**2. 消息数量层面**：
- **Supervisor 最多保留 40 条消息**（`_SUPERVISOR_MAX_MESSAGES`）
- 超出时：保留第一条（system prompt/research brief）+ 最近的消息
- **ThinkTool 反思优先保留**：因为 ThinkTool 携带高信号结构化信息（gaps/confidence/strategy）
- 丢弃最旧的中间结果

**3. Researcher 层面**：在 `middleware/shared.py` 中 `enforce_context_budget()` 额外限制：
- 最多 30 条消息
- ConductResearch 结果截断到 8K

---

### Q9：Context Engineering 中，你们的系统提示词是怎么设计的？怎么避免随着迭代导致提示词越来越长？

**A**：采用 **context prefix 模式**：

- 系统提示词和 research brief **不作为 state 的一部分存储**
- 每次 Supervisor/Researcher 节点执行时，从模板重新构建 system prompt + research brief 作为前缀
- 只有 AI 响应和 ToolMessage **存储在 state 的 supervisor_messages/researcher_messages 中**
- 这样每条消息只包含当次迭代的上下文，系统提示词始终是固定长度的模板

**对比传统做法**：如果把 system prompt 放进 messages 列表并持久化，每次迭代都会重复存储，浪费 token。

---

## 4. 状态管理与 LangGraph

### Q10：解释一下 `override_reducer` 的设计。为什么不用普通的 `operator.add`？

**A**：LangGraph 的默认 reducer 是 `operator.add`（累加）。对于 `supervisor_messages` 这样的字段，如果每次节点返回都累加，会导致消息列表越来越长，且无法实现"完全替换"的语义。

`override_reducer` 的设计：

```python
def override_reducer(current_value, new_value):
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)  # 完全替换
    return operator.add(current_value, new_value)  # 累加
```

**使用方式**：当需要完全替换时，返回 `{"type": "override", "value": [...]}`；正常情况累加。

**典型场景**：Supervisor 完成一轮迭代后，要更新整个 supervisor_messages 列表（旧消息 + 新响应的组合），而不是只追加新响应。

---

### Q11：你们有 AgentState、SupervisorState、ResearcherState 三个 TypedDict，它们之间是什么关系？为什么需要三个而不是一个？

**A**：这是 **子图嵌套的边界隔离** 设计：

- **AgentState**：主图状态，包含从 Input Gateway → Supervisor → Report 的全生命周期字段（30+ 字段）
- **SupervisorState**：监督者子图状态，**仅包含 Supervisor 关心** 的字段（supervisor_messages、research_brief、notes 等）
- **ResearcherState**：研究者子图状态，**仅包含单个 Researcher 关心** 的字段（researcher_messages、research_topic、compressed_research 等）

**为什么需要三个？**

1. **职责隔离**：Researcher 不需要知道 `complexity`、`quality_summary` 等主图字段
2. **安全边界**：子图不能意外修改父图状态
3. **可测试性**：每个子图可以独立测试，只需要构造自己的 State
4. **类型安全**：TypedDict 保证每个节点输入输出的字段是显式的
5. **复用**：同一个 Researcher 子图被多个 Supervisor call 并行复用，每次传入不同的 ResearcherState

---

### Q12：LangGraph 的 interrupt 机制你是怎么用的？HITL 的具体流程是怎样的？

**A**：LangGraph 的 `interrupt()` 在 `research_plan.py` 的 `plan_research()` 节点中使用：

**流程**：
1. `plan_research()` 使用 strategic_llm 生成研究计划（子主题、搜索策略、来源偏好、预期输出、置信度）
2. 调用 `interrupt()` 暂停图执行，等待外部输入
3. 用户通过 API/UI 传入三种操作之一：
   - **approve**：继续执行，进入 research_supervisor
   - **revise**：用 fast_llm 根据用户反馈修订计划，再次 interrupt
   - **cancel**：返回 Command(goto="__end__") 直接结束图

**为什么需要**：Supervisor + 多个 Researcher 的并行执行非常昂贵（token 成本高、耗时长）。在投入大量资源之前让用户确认研究方向是否正确，**显著降低浪费**。这是 Google Gemini 的 "plan first, approve, then execute" 模式。

---

## 5. 模型路由与成本优化

### Q13：你们的三层模型路由 (fast/smart/strategic) 是怎么决策的？8种任务类型的映射关系是什么？

**A**：三模型决策逻辑在 `ResearchConfiguration.get_model_for_task()` 中：

1. **先检查专用覆盖字段**（如 `query_generation_model`），如果配置了就用
2. **再查 `_TASK_FALLBACK_MAP`** 按任务类型映射到复杂度层

**8种任务类型映射**：

| 任务类型 | 默认模型层 | 原因 |
|---------|-----------|------|
| query_generation | **fast_llm** | 高频、低价值，只需生成搜索关键词 |
| content_summarization | **fast_llm** | 最高频，单页面摘要无需深度理解 |
| web_reading | **fast_llm** | 提取事实，机械性工作 |
| result_synthesis | **smart_llm** | 需要理解和综合多个结果 |
| strategic_decision | **strategic_llm** | 决策质量直接影响整体产出 |
| compression | **smart_llm** | 语义压缩需要理解能力 |
| report_writing | **smart_llm** | 报告质量是最终交付物 |
| quality_check | **fast_llm** | 低成本快速检查 |

**设计原理**（来自 Anthropic Building Effective Agents）：**LLM 在执行独立子任务时表现更好**。把任务按认知负载拆分，将便宜模型用于机械性工作，将昂贵模型用于推理密集型决策。

---

### Q14：为什么要做模型路由？不能所有任务都用最强的模型吗？

**A**：三个原因：

1. **成本**：一次 Deep Research 可能涉及 50+ 次搜索、10+ 次页面阅读、N 次压缩。全部用 strategic_llm（如 GPT-4.1）的成本是直接用 fast_llm 做摘要的 **10-20 倍**。
2. **延迟**：fast_llm 通常比 strategic_llm 快 3-5 倍。大量并行任务用慢模型会导致整体延迟不可接受。
3. **认知负载匹配**：让 strategic_llm 做 "提取这个网页的三个关键事实" 是浪费。**用对的工具做对的事**。

**量化示例**：假设一次 Deep Research 有 30 次 web_reading + 5 次 synthesis + 1 次 report。用 gpt-4.1-mini 做 web_reading（$0.15/1M input），gpt-4.1 做 report（$2/1M input），比全部用 gpt-4.1 省约 **60-70% 的 API 费用**。

---

### Q15：你是怎么估算和控制整个研究过程的 token 消耗的？

**A**：通过 `TokenUsageTracker` 实现按阶段追踪：

- **记录维度**：每个 LLM 调用记录 `input_tokens` + `output_tokens`
- **阶段分类**：supervisor / research / report 三个独立 phase
- **内置定价表**：`_MODEL_PRICING` 覆盖 DashScope Qwen、OpenAI、DeepSeek 等
- **成本估算**：`estimate_cost(model_name, input_tokens, output_tokens)` 按 1M tokens 单价计算

**在代码中的埋点位置**：
- Supervisor 节点每次 LLM 调用后 `tracker.record("supervisor", input, output)`
- Researcher 节点类似，记为 "research"
- Report 节点记为 "report"

**为什么不直接用 LangSmith？**LangSmith 是事后分析工具，TokenUsageTracker 提供**实时成本感知**，可以在超预算时触发告警或降级。

---

## 6. 记忆系统 (Memory System)

### Q16：你们的双重模式记忆系统是怎么设计的？结构化记忆和语义记忆分别做什么？

**A**：双模式设计来自 deer-flow + gpt-researcher 的融合：

**结构化记忆（deer-flow 风格）**：
- **存储**：`users/{user_id}/memory.json`（per-user 文件隔离）
- **内容**：
  - `UserContext`：角色、偏好、专家级别、语言偏好、格式偏好、详细程度偏好
  - `Facts[]`：从研究历史中 LLM 提取的事实
  - `ResearchHistory`：过往查询和结果摘要（保留最近 20 条）
- **用途**：**个性化**——在 clarify 阶段注入用户偏好，在 report 阶段按用户格式习惯输出

**语义记忆（gpt-researcher 风格）**：
- **存储**：`users/{user_id}/embeddings/` 目录下的 JSON 文件（生产环境可替换为向量数据库）
- **内容**：用 `text-embedding-3-small` 对事实和研究结果做 embedding
- **用途**：**相似研究复用**——当用户提类似问题时，通过嵌入相似度检索过往发现

**记忆注入点**：
1. Clarify Node → 用户偏好（角色、来源偏好）
2. ResearchBrief Node → 研究历史（之前研究过什么）
3. Researcher System Prompt → top-N 相关事实
4. Report Generator → 用户格式/语言偏好

**当前局限**：语义检索目前是**关键字匹配**（简单但可靠），嵌入搜索是预留接口。因为对于记忆量不大的场景，关键字匹配已经够用。

---

### Q17：记忆更新是同步还是异步的？怎么保证不阻塞报告生成？

**A**：**Fire-and-forget 异步模式**。

在 `report.py` 的 `final_report_generation()` 中：

```python
await memory_mw.update_memory(...)  # 用 try/except 包裹
```

`MemoryMiddleware.update_memory()` 是一个**异步调用但不阻塞**的设计：
- 先同步写入 JSON 文件（轻量操作，<10ms）
- 语义索引更新是 `await` 但在后台执行
- 即使记忆更新失败，**报告照样返回用户**（通过 try/except 兜底）
- 去抖动队列 `MemoryUpdateQueue` 防止短时间内重复写入

---

## 7. 质量保障与评估体系

### Q18：你们的三级评估体系具体是怎么分的？每级干什么？

**A**：

**Level 1：即时质量检查（fast_llm，<5s）**
- 每次报告生成后自动运行
- **6个维度**：citation_density、section_completeness、format_correctness、topic_relevance、minimum_length、evidence_alignment
- **自动修订**：score < 0.7 触发 revise → re-check，最多 2 次
- 支持 rubric 系统（结构化评分 + 主张对齐检查）

**Level 2：9维度加权评估（smart_llm）**
- 开发阶段运行（Pytest 集成）
- 加权评分：topic_relevance_overall=1.5, section_relevance_critical=2.0 等
- 比 Level 1 更细粒度、更多维度

**Level 3：4维度深度评估（strategic_llm）**
- 仅对 deep 复杂度任务运行
- 4 维度 1-5 评分：coverage(0.30), accuracy(0.25), freshness(0.20), coherence(0.25)
- **退化检测**：如果 Level 3 分数显著低于 Level 1/2，标记为退化

**为什么要三级**：Level 1 是**成本最低的即时反馈**，Level 2 是**开发期的全面检查**，Level 3 是**关键任务的深度保障**。根据任务复杂度适配评估深度，不浪费资源。

---

### Q19：Evidence Alignment（主张对齐）具体怎么做的？怎么检查 LLM 有没有编造引用？

**A**：在 `quality_check.py` 中实现：

1. **提取引用**：用正则提取报告中的 `[N]` 引用标记
2. **提取源文本**：从 research notes 中提取 cited sources 的原文片段
3. **LLM-as-Judge**：将报告中的主张 + 对应的源文本一起发给 fast_llm，要求判断：
   - 主张是否真实被源文本支持（supported/partial/unsupported）
   - 给每个主张打分（0.0-1.0）
4. **对齐率计算**：supported 主张数 / 总主张数

**对齐率阈值**：`evaluation_claim_alignment_min_rate`（默认 0.75）。低于阈值 → revise

**局限性**：LLM-as-Judge 本身也可能出错（二次幻觉）。这是当前研究领域的开放问题，Weaver 的做法是**只对有明显矛盾的主张判定为 unsupported，边界情况给 partial**。

---

## 8. MCP、Skills 与工具系统

### Q20：MCP 在 Weaver 中是怎么集成的？什么时候会用 MCP 工具而不是内置工具？

**A**：MCP 工具加载在 `_get_researcher_tools()` 中：

```python
if research_config.mcp_enabled:
    from tools.mcp import init_mcp_tools as _init_mcp_tools
    mcp_tools = await _init_mcp_tools(config)
    tools.extend(mcp_tools)
```

**使用场景**：
- 内置工具（Tavily、DuckDuckGo、ArXiv、PubMed、沙箱 Shell/Code）是**通用研究工具**
- MCP 工具是**用户自定义的扩展**，例如：
  - 企业内部 API（CRM、ERP、数据库查询）
  - 专业数据源（Bloomberg、Wind）
  - 第三方服务（Slack、GitHub、Jira）

**OAuth 安全**：`mcp/oauth.py` 的 `OAuthTokenManager` 自动管理 token 刷新和 Authorization header 注入。

**MCP 和 Function Call 的关系**：MCP 是**工具发现的标准化协议**（N+M 问题），Function Call 是**LLM 调用工具的具体机制**。Weaver 用 MCP 协议连接外部工具，用 LangChain 的 bind_tools + tool_calls 执行。

---

### Q21：Skills 系统和 System Prompt 有什么区别？在 Weaver 中 Skills 是怎么加载和生效的？

**A**：

| 维度 | System Prompt | Skills |
|------|-------------|--------|
| 作用范围 | 全局，一直生效 | **按需激活**，场景触发 |
| 内容 | 通用行为规范 | **特定领域专业指导** |
| 可维护性 | 随功能增多变复杂 | **模块化，各自独立** |
| 示例 | "你是一个研究助手..." | "代码审查 Skill：检查安全/性能/代码质量" |

**Weaver 中 Skills 的加载流程**：

1. **解析**：`parser.py` 解析 SKILL.md 的 YAML front-matter，提取 name、description、allowed-tools
2. **工具白名单**：`tool_policy.py` 汇总所有 Skill 声明的 allowed-tools，过滤 Researcher 可用工具
3. **上下文注入**：
   - `prompt.py` 的 `get_skills_prompt_section()` 生成 `<skill_system>` 提示段
   - `build_skill_context()` 按阶段（research/writing）从 Skill 文件中提取相关章节注入到提示词

**关键设计**：Skills **不增加 LLM 调用次数**，而是**在现有调用中注入领域知识**。这比用 Multi-Agent 让一个 "专家 Agent" 参与讨论更高效。

---

## 9. Agent 通用八股文

### Q22：LLM 和 Agent 的本质区别是什么？

**A**：**LLM 是"大脑"（推理引擎），Agent 是"完整的执行者"**。

| 维度 | LLM | Agent |
|------|-----|-------|
| 能力边界 | 只能输出文本 | 可以执行动作 |
| 记忆 | 无状态（每次调用独立） | 有短期+长期记忆 |
| 工具 | 无 | 可以调用 API、搜索、执行代码 |
| 规划 | 线性输出 | 多步规划+反思 |
| 本质 | 条件概率模型 P(token_n \| context) | **LLM + 工具 + 记忆 + 规划循环** |

**一个例子说清楚**：用户说"帮我查北京天气，如果下雨就取消跑步计划"。LLM 告诉你"可以打开天气 App 查询..."，Agent 直接**调用天气 API → 查到中雨 → 调用日历 API → 删除跑步计划 → 回复已完成**。

---

### Q23：ReAct 模式是什么？怎么避免死循环？

**A**：ReAct = **Reasoning + Acting**（推理+行动交替）。

核心循环：**Thought → Action → Observation → Thought → ...** 直到任务完成。

**避免死循环的三个方法**（面试高频）：

1. **最大步数限制**：如 Weaver 的 `max_react_tool_calls`（默认 8），超过强制终止
2. **重复动作检测**：Weaver 的 `LoopDetector` 双层检测：
   - MD5 哈希检测：连续 N 次完全相同响应
   - 前缀频率检测：前 100 字符重复次数超过阈值
3. **超时控制**：整个任务设置最大执行时间

Weaver 的实现：`check_loop()` 在每次 Researcher/Supervisor LLM 调用前执行，检测到循环后**直接强制进入压缩/结束阶段**，而不是继续循环。

---

### Q24：Function Call 的底层原理是什么？LLM 自己执行函数吗？

**A**：**LLM 不执行函数，它只输出结构化的"我想调用什么"的指令**。

**四步流程**：
1. **定义工具**：通过 JSON Schema 告诉 LLM 可用的函数名、参数、描述
2. **LLM 判断并输出**：LLM 输出不是文本，是结构化 JSON（tool_calls），包含函数名和参数
3. **你的代码执行**：解析 tool_calls，真正调用 API/数据库
4. **结果回传**：把执行结果作为 ToolMessage 追加到对话历史，LLM 基于结果继续推理

**为什么 LLM 不自己执行**：安全——LLM 无法绕过你的代码直接操作系统。这是**沙箱安全的核心设计**。

---

### Q25：MCP 协议解决的核心问题是什么？和 A2A 的区别是什么？

**A**：

**MCP 解决的核心问题**：**N × M 爆炸**。N 个 AI 应用 × M 个工具 = N × M 套集成代码。MCP 把问题变成 N + M（每个应用只需实现 MCP Client，每个工具只需实现一个 MCP Server）。

**MCP vs A2A**：

| 维度 | MCP | A2A |
|------|-----|-----|
| 解决的问题 | Agent ↔ 工具（纵向集成） | Agent ↔ Agent（横向协作） |
| 通信方向 | 一个 Agent 调用多个外部服务 | 多个 Agent 之间互相委托任务 |
| 核心机制 | tools/list 工具发现 | Agent Card 能力声明 |
| 当前状态 | 已是事实标准 | 早期阶段（Google 推动） |

**两者互补，不替代**：Agent 内部用 MCP 调工具，Agent 之间用 A2A 协作。

---

### Q26：Agent 的记忆系统怎么分层设计？

**A**：两层结构：

**1. 上下文窗口（In-Context Memory）**
- 当前对话、任务状态、工具调用历史、注入的 Skill 和长期记忆
- 限制：上下文窗口大小（如 128K tokens）
- 超出窗口时需压缩或归档

**2. 外部记忆（External Memory）**

| 类型 | 存什么 | 检索方式 | Weaver 实现 |
|------|--------|---------|------------|
| 结构化存储 | 用户偏好、研究历史、提取的事实 | 关键字/时间 | JSON 文件 |
| 向量数据库 | 历史研究的语义内容 | 嵌入相似度 | embedding JSON + 预留向量库 |
| 知识图谱 | 实体关系 | 图查询 | 未实现 |

**Weaver 的双模式**：结构化（快速精确）+ 语义（模糊相关），两者互补。

---

### Q27：RAG 和 Agent 是什么关系？

**A**：**RAG 是 Agent 的一个工具/能力，不是对立概念**。

- **RAG**：检索增强生成——先检索相关文档，再把文档作为上下文输入 LLM。本质是解决 LLM 知识截止和幻觉问题。
- **Agent**：自主感知环境、规划、执行动作的智能体。Agent 可以使用 RAG 作为其中一个信息获取工具。

**在 Weaver 中的体现**：
- Researcher 的搜索工具（Tavily、ArXiv、PubMed）本质就是 RAG 的 retrieval 部分
- Researcher 的工具调用循环就是 Agent 使用 RAG 的模式
- 来源路由 `source_routing.py` 的 RAG 模式允许用户指定从私有文档库检索

**面试加分点**：高级 RAG 不止是 naive retrieval，还包括 query rewriting、hybrid search、re-ranking、self-reflection（检索结果不好时改写 query 再试）。

---

### Q28：Agent 和 Workflow 的区别？什么时候用哪个？

**A**：

**核心区别**：**Workflow 的控制权在代码手里，Agent 的控制权在 LLM 手里**。

| 维度 | Workflow | Agent |
|------|----------|-------|
| 流程控制 | 开发者预定义 if/else | LLM 自主决策下一步 |
| 可预测性 | 高 | 低 |
| 灵活性 | 低 | 高 |
| Token 消耗 | 低 (~1x) | 高 (~4-8x) |
| 调试难度 | 容易 | 困难 |
| 适合场景 | 固定流程（订单处理、审批） | 开放式目标（研究分析、客服） |

**实际生产**：**混合架构最主流**。Weaver 本身就是一个混合架构——Input Gateway 是 Workflow（固定顺序），Supervisor 循环是 Agent（LLM 自主决策）。

---

## 10. 系统设计与场景追问

### Q29：如果用户想研究一个非常宽泛的话题（如"AI 对教育的影响"），你的系统怎么做范围控制？

**A**：三个层面的范围控制：

1. **Clarify 阶段**：如果 `allow_clarification=True`，系统会先判断是否需要澄清，追问用户具体关心的角度（K12？高等教育？职业培训？）

2. **Complexity 分类**：宽泛话题会被分类为 deep 复杂度，进入完整 Supervisor 循环

3. **Research Plan 阶段**：HITL 计划门——用 strategic_llm 先生成研究计划（子主题拆分 + 搜索策略），用户 approve 后才执行。用户可以在此阶段调整方向。

4. **Supervisor ThinkTool**：如果 Supervisor 发现研究发散，可以通过 ThinkTool 的 `gaps_identified` 和 `next_strategy` 自动收窄方向

---

### Q30：如果同时有 1000 个用户并发使用 Weaver，你会怎么设计？瓶颈在哪里？

**A**：

**瓶颈分析**：
1. **LLM API 速率限制**：最直接的瓶颈。OpenAI/阿里云的 API 有 RPM/TPM 限制
2. **搜索引擎配额**：Tavily/Bocha 等有月度配额和并发限制
3. **内存和 CPU**：每个并发请求有独立的 StateGraph 实例和消息历史
4. **沙箱资源**：E2B/Daytona 沙箱有限

**优化策略**：
- **请求级隔离**：LangGraph 的 thread_id 天然支持多租户隔离，每个用户的图实例独立
- **速率限制**：`common/rate_limiter.py` 的 token bucket 限制器，按用户/全局维度限流
- **搜索缓存**：`agent/core/search_cache.py` 的 LRU 缓存 + 查询去重，减少重复搜索
- **模型降级**：高负载时 dynamic routing 可以降级到更便宜/更快模型
- **异步非阻塞**：Memory 更新、EventEmitter 都是异步 fire-and-forget，不阻塞主流程
- **PostgreSQL 持久化**：checkpointer 支持跨进程状态恢复，配合连接池

---

### Q31：你们怎么处理 LLM 幻觉？有哪些具体的兜底机制？

**A**：Weaver 的多层幻觉防御：

1. **来源强制关联**：报告要求每个主张附带引用 `[N]`，没有引用的内容在 Level 1 质量检查中扣分
2. **Evidence Alignment 检查**：LLM-as-Judge 验证报告中的主张是否被源文本支持
3. **搜索工具返回原始内容**：不依赖 LLM 记忆，每次都从搜索结果中提取事实
4. **ThinkTool 反思**：Supervisor 和 Researcher 都会反思发现的矛盾
5. **压缩时保留引用**：`COMPRESSION_SIMPLE_HUMAN_MESSAGE` 明确要求 "preserve all information and add citation markers"
6. **质量检查自动修订**：如果 Level 1 检测到 evidence_alignment 分数低，触发 revise 重新生成

**工程兜底**：承认 LLM-as-Judge 也可能出错（二次幻觉）。当前的做法是容忍部分边界性错误，但对明显矛盾（主张和源文本直接冲突）严格拦截。

---

### Q32：LangGraph 的 StateGraph 中，你是怎么做错误恢复的？一个 Researcher 崩了会影响整个 Supervisor 吗？

**A**：

1. **Tool Error Handling**：所有工具调用都经过 `ToolErrorHandler.execute_with_error_handling()`，工具失败返回 ToolMessage 而非 Exception。一个搜索失败不会崩溃整个流程。

2. **Researcher 隔离**：每个 Researcher 是独立子图。如果单个 Researcher 抛出异常，`asyncio.gather` 会捕获它，Supervisor 收到的是错误 ToolMessage。**其他 Researcher 不受影响**。

3. **LLM 调用重试**：使用 LangChain 的 `.with_retry(stop_after_attempt=3)`，LLM 调用失败自动重试。

4. **结构化输出重试**：Pydantic 解析失败重试最多 `max_structured_output_retries`（默认 3）次。

5. **整个图的重试**：PostgreSQL checkpointer 支持从断点恢复，整个图可以从失败的节点重新开始。

6. **A/B 测试**：`run_recovery_ab_test()` 对比有/无错误处理的恢复率。

---

### Q33：你怎么衡量 Weaver 系统的质量？有没有做过 Benchmark？

**A**：支持 **GAIA Benchmark**：

- 图中有 `gaia_answer` 节点，专用于输出短答案（符合 GAIA 评估格式）
- 通过 `configurable.gaia_mode` 开关控制
- Supervisor 完成后走 GAIA 短答案路径而非完整报告

**内部评估体系**：
- **Level 1**：每次运行自动评分
- **Level 2**：开发阶段 Pytest 集成，9 维度加权评分
- **Level 3**：深度任务的手动/LLM 深度评估

**实际质量指标**：citation_density、evidence_alignment、topic_relevance、quality_gates 通过率等。

---

### Q34：如果让你从头重新设计 Weaver，你会做什么不同的选择？

**A**：

1. **向量数据库替代 JSON 嵌入文件**：当前语义记忆用 JSON 文件存储 embedding，生产环境应使用 Milvus/Qdrant/Weaviate
2. **更好的评估体系**：当前 LLM-as-Judge 做主张对齐有一定误差，可以考虑引入结构化知识图谱做校验
3. **流式输出的精细控制**：当前报告生成是一次性输出，应该支持 SSE 逐 token 流式（已在 EventEmitter 中有基础）
4. **更灵活的 Source Routing**：当前是基于配置的策略路由，可以引入 LLM 自主判断使用 web/rag/hybrid
5. **A2A 协议支持**：当前是单 Agent（Orchestrator-Workers 是内部子图），未来可能需要 A2A 与外部 Agent 协作
6. **更好的可观测性**：当前只有 LangSmith tracing，可以增加 OpenTelemetry 和更细粒度的 metric

---

### Q35：未来的几个技术方向你怎么看？比如 agent 的自我进化、长时任务执行、agent swarm？

**A**：

1. **Agent 自我进化（Self-Improving）**：当前 Weaver 的记忆系统是基础——记录结果、下次检索。未来的方向是通过 RLHF 或 preference optimization 让 agent 从错误中学习，而不仅是记录事实。

2. **长时任务执行（Long-Running Tasks）**：Deep Research 本身就是一个长时任务。关键挑战是上下文窗口管理和中间状态持久化。Weaver 通过压缩 + checkpointer 部分解决了，但还需要更好的任务分解和并行化。

3. **Agent Swarm（智能体集群）**：当前 Orchestrator-Workers 是集中式的。去中心化 Swarm 更适合高度动态和不确定的环境，但调试和控制难度剧增。Anthropic 的建议仍然适用——不要过早引入。

4. **MCP 生态的愿景**：MCP 解决了工具接口标准化，未来可能出现 **Agent App Store** 模式——任何人都可以发布 MCP Server，Agent 可以动态发现和使用任意工具。

5. **成本和延迟优化**：MoE 模型、speculative decoding、语义缓存、请求批处理——这些工程优化对于大规模部署同样重要。

---

## 附录：面试速记 Checklist

- [ ] LLM vs Agent 四大区别：会做 vs 会说、有记忆 vs 无状态、能用工具 vs 纯文本、能规划 vs 线性输出
- [ ] Agent 四模块：LLM（大脑）、规划（拆解）、记忆（存储）、工具（执行）
- [ ] ReAct 三步循环：Thought → Action → Observation
- [ ] 防死循环三招：最大步数、重复检测、超时控制
- [ ] Function Call 四步：定义工具 → LLM 输出指令 → 代码执行 → 结果回传
- [ ] MCP 解决 N×M 爆炸 → N+M
- [ ] MCP vs A2A：纵向工具集成 vs 横向 Agent 协作
- [ ] Skills vs System Prompt：按需激活 vs 全局生效
- [ ] Agent vs Workflow：LLM 控制流程 vs 代码控制流程
- [ ] 记忆分层：上下文窗口（短期）+ 外部存储（长期：结构化/向量/图谱）
- [ ] Orchestrator-Workers vs 去中心化多 Agent：Anthropic 建议优先单 Agent
- [ ] 上下文预算控制：截断 + 消息数限制 + 高信号优先保留
- [ ] 三级模型路由：fast（机械）/smart（综合）/strategic（推理）
- [ ] 幻觉防御：引用强制 + Evidence Alignment + LLM-as-Judge + 自动修订