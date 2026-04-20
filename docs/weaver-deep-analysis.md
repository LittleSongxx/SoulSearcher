# Weaver 项目深度分析

## 一、项目定位

Weaver 是一个**基于 LangGraph 的 AI 智能体平台**，核心聚焦于 **Deep Research**（深度研究）能力。通过智能路由、并行搜索、树状探索、知识压缩、质量评估、事实验证等一系列精密流水线，将用户查询转化为高质量的研究报告。

---

## 二、系统架构总览

```
用户入口 → Next.js Web UI → FastAPI → SmartRouter → LangGraph StateGraph
                                                      ├── direct_answer
                                                      ├── agent (工具调用)
                                                      ├── web (快速搜索)
                                                      ├── deep (深度研究)
                                                      └── clarify (追问)
```

| 层 | 关键目录 | 职责 |
|---|---|---|
| **前端** | `web/` | Next.js 14 + React 18 + Tailwind + Radix UI |
| **API 层** | `main.py` (单体) | FastAPI，SSE 流式，HITL 审批，Prometheus |
| **Agent 内核** | `agent/core/`, `agent/workflows/` | LangGraph 图定义、节点实现、路由、深度搜索 |
| **工具层** | `tools/` | 搜索引擎、沙箱、浏览器、爬虫、RAG、导出、MCP |
| **共享层** | `common/` | 配置、并发、取消、Session、Tracing、SSE |
| **触发器** | `triggers/` | 定时(Cron)、Webhook、事件触发 |
| **基础设施** | PostgreSQL (pgvector) + Redis | Checkpoint 持久化 + 长期记忆 |

---

## 三、核心技术栈

### 后端

- **Python 3.11+**, `FastAPI 0.134` + `uvicorn`
- **LangGraph 1.x** + `langchain 1.x` + `langchain-openai 1.x` — 图编排引擎
- **PostgreSQL 16 + pgvector** — Checkpoint 持久化 + 向量搜索
- **Redis** — 缓存、长期记忆后端
- **mem0ai** — 用户长期记忆（可选）
- **Pydantic v2** + `pydantic-settings` — 类型安全配置
- **Prometheus** — `/metrics` 端点，运行指标
- **E2B** — 云端沙箱（代码执行、浏览器、文件操作）
- **Daytona** — 远程沙箱（可选）
- **Playwright** — 浏览器自动化 + 爬虫
- **ChromaDB** — 本地 RAG 向量存储
- **tiktoken** — Token 精确计数

### 前端

- **Next.js 14** + **React 18** + **TypeScript**
- **Tailwind CSS** + **Radix UI** + **Lucide Icons** + **shadcn/ui** 风格组件
- **react-virtuoso** — 虚拟滚动（长代码输出）
- **react-markdown** + **remark-gfm** + **rehype-katex** — Markdown + LaTeX 渲染
- **Mermaid** — 图表渲染
- **SSE** — 实时流式事件

### SDK

- `sdk/python/` — Python SDK
- `sdk/typescript/` — TypeScript SDK + OpenAPI types 自动生成

---

## 四、LangGraph 图引擎 — 核心架构

### 4.1 AgentState — 超丰富的状态模型

`AgentState`（`agent/core/state.py`）是一个 `TypedDict`，包含 **18 个分组、40+ 字段**：

| 分组 | 关键字段 |
|---|---|
| **Input/Output** | `input`, `images`, `final_report`, `draft_report` |
| **Routing** | `route`, `routing_reasoning`, `routing_confidence`, `suggested_queries` |
| **Execution** | `messages` (auto-trimmed), `research_plan`, `current_step`, `status` |
| **Research Data** | `scraped_content`, `code_results`, `summary_notes`, `sources` |
| **Quality Control** | `evaluation`, `verdict`, `eval_dimensions`, `missing_topics`, `revision_count` |
| **Tool Control** | `tool_approved`, `pending_tool_calls`, `tool_call_count`, `enabled_tools` |
| **Research Tree** | `research_tree`, `current_branch_id`, `tree_exploration_enabled` |
| **Domain** | `domain`, `domain_config` |
| **Compressed Knowledge** | `compressed_knowledge` |
| **Metrics** | `total_input_tokens`, `total_output_tokens` |

消息列表使用 `capped_add_messages` 自动裁剪：保留前 N 条 + 后 M 条，中间可选摘要压缩。

### 4.2 LangGraph 图定义

图节点和边定义（`agent/core/graph.py`）：

```
START → router
  ├─ direct → direct_answer → human_review → END
  ├─ agent → agent → human_review → END
  ├─ web → web_plan → hitl_plan_review → search → writer → human_review → END
  ├─ deep → deepsearch → human_review → END
  │   (或 hierarchical 模式):
  │   deep → coordinator → planner → hitl_plan_review → search →
  │          compressor → hitl_sources_review → writer → hitl_draft_review →
  │          evaluator → [pass: human_review | revise: reviser → evaluator | incomplete: refine_plan]
  └─ clarify → human_review → END
```

**关键节点**：

- **router** — SmartRouter LLM 分类
- **planner** — 研究计划生成
- **perform_parallel_search** — 并行搜索执行
- **compressor** — 知识压缩（事实提取、去冗余）
- **writer** — 报告撰写
- **evaluator** — 多维质量评估（coverage, accuracy, freshness, coherence）
- **reviser** — 报告修订
- **coordinator** — 分层模式协调器（plan/research/synthesize/reflect/complete）
- **hitl_\*_review** — HITL（Human-in-the-Loop）审批节点

### 4.3 SmartRouter — 五路智能分发

`agent/core/smart_router.py` 定义五种路由类型：

- **`direct`** — 简单知识问答，直接回答
- **`agent`** — 需要工具调用的多步任务
- **`web`** — 快速网络搜索（时效信息）
- **`deep`** — 深度研究（综合多搜索+迭代）
- **`clarify`** — 模糊查询，需要追问

使用 LLM `with_structured_output(RouteDecision)` 结构化输出，置信度评分 + fallback 机制。还包含关键词级别的 `detect_tool_requirements()` 快速检测所需工具类别（python/browser/web_search/files/shell）。

---

## 五、Deep Research — 最大亮点

### 5.1 双模式深度搜索

`agent/workflows/deepsearch_optimized.py` 实现了优化版深度搜索，关键改进：

1. URL 去重机制
2. 详细性能日志
3. 增强错误处理
4. 取消支持
5. OOP 封装
6. 树状探索
7. 多模型支持

**三种模式**: `auto` / `tree` / `linear`。Auto 模式自动判断：简单事实用 linear，复杂研究用 tree。

### 5.2 树状探索（Tree Explorer）

`agent/workflows/research_tree.py` 灵感来自 GPT Researcher 的树探索：

- **主题分解** — LLM 将研究主题拆分为子主题树
- **并行探索** — 多分支并行搜索（`tree_parallel_branches` 控制并发）
- **深度限制** — `tree_max_depth=2`，`tree_max_branches=4`
- **预算控制** — `deepsearch_tree_max_searches=30` 硬上限
- **相关性评分** — 每个节点有 `relevance_score` 指导优先级
- **状态跟踪** — `NodeStatus`: pending → in_progress → completed/failed/skipped

```python
@dataclass
class ResearchTreeNode:
    id: str
    topic: str
    depth: int
    parent_id: Optional[str]
    children_ids: List[str]
    status: NodeStatus
    findings: List[Dict[str, Any]]
    sources: List[str]
    summary: str
    queries: List[str]
    relevance_score: float
```

### 5.3 IterDRAG 知识缺口分析

`agent/workflows/knowledge_gap.py` 中 `KnowledgeGapAnalyzer` 在每轮搜索后：

1. 分析当前已收集信息的覆盖率
2. 识别缺失维度（定义、历史、应用、优缺点、趋势等）
3. 生成针对性补充查询
4. 输出 `overall_coverage` 和 `confidence` 评分

### 5.4 知识压缩（Compressor）

`agent/workflows/compressor.py` 灵感来自 Open Deep Research：

- 从原始搜索结果中**提取关键事实** + 来源引用
- **识别统计数据**和量化信息
- **去冗余和矛盾检测**
- 按**子主题结构化**组织

```python
@dataclass
class CompressedKnowledge:
    topic: str
    facts: List[ExtractedFact]
    statistics: List[Dict[str, Any]]
    key_entities: List[str]
    subtopics: Dict[str, List[ExtractedFact]]
    summary: str
```

### 5.5 质量评估 + 事实验证

`agent/workflows/quality_assessor.py` 提供 `QualityReport`：

- **Claim 验证** — 从报告中提取事实性声明，匹配来源验证
- **矛盾检测** — 报告内部一致性检查
- **来源多样性评分** — 域名级别多样性
- **引用准确性** — 引用覆盖率和正确性

评分维度：

```python
@dataclass
class QualityReport:
    claim_support_score: float
    source_diversity_score: float
    contradiction_free_score: float
    citation_accuracy_score: float
    citation_coverage_score: float
    overall_score: float
```

`ClaimVerifier`（`agent/workflows/claim_verifier.py`）使用语义匹配（方向性词对检测，如"增长"vs"下降"），支持中英双语。

### 5.6 证据段落管理

`evidence_passages.py` 将长文本切分为带偏移量的段落，`source_registry.py` 全局注册来源，确保引用可追溯。

### 5.7 可视化规划

`agent/workflows/viz_planner.py` 自动从压缩知识中检测数据模式，推荐图表类型（Bar/Line/Pie/Comparison/Timeline/Table），用 Matplotlib 生成 Base64 图表嵌入报告。

---

## 六、多模型 + 多阶段路由

### 6.1 Task-Type 模型路由

`agent/core/multi_model.py` 支持不同研究阶段配置不同模型：

| TaskType | 配置项 | 默认 |
|---|---|---|
| `ROUTING` | `reasoning_model` | o1-mini |
| `PLANNING` | `planner_model` | reasoning_model |
| `RESEARCH` | `researcher_model` | primary_model |
| `WRITING` | `writer_model` | primary_model |
| `EVALUATION` | `evaluator_model` | reasoning_model |
| `CRITIQUE` | `critic_model` | reasoning_model |

支持 OpenAI / Anthropic / Azure / Ollama / DeepSeek / Custom 6 种 Provider，带 fallback 链和成本/延迟追踪。

### 6.2 领域路由

`agent/workflows/domain_router.py` 支持 8 个研究领域:

- scientific / legal / financial / technical / medical / business / historical / general

每个领域预配置推荐来源和搜索前缀。例如 scientific 推荐 arxiv.org、scholar.google.com、pubmed 等。

---

## 七、搜索系统 — 12+ Provider 聚合

### 7.1 Multi-Search 聚合引擎

`tools/search/multi_search.py` 灵感来自 DeerFlow：

**通用搜索 Provider**:

- Tavily, DuckDuckGo, Brave, Serper, SerpAPI, Bing, Exa, Google Custom Search, Bocha, DashScope

**实时 Feed Provider** (`tools/search/feeds/`):

- Twitter/X (tweepy), Reddit (praw), HackerNews

**学术搜索 Provider** (`tools/search/academic/`):

- arXiv, Semantic Scholar, PubMed

**搜索策略** (`search_strategy`): `fallback` / `parallel` / `round_robin` / `best_first`

### 7.2 可靠性保障

`tools/search/reliability.py` 中 `ProviderReliabilityManager`：

- **自动重试** — 指数退避 (`retry_backoff_seconds=0.5`)
- **断路器** — N 次连续失败后自动断开，超时后重置
- **失败降级** — 返回空结果而非抛异常

### 7.3 搜索缓存

`agent/core/search_cache.py` 中 `SearchCache` — 线程安全 LRU 缓存：

- TTL 过期（默认 1 小时）
- **语义相似度匹配**（SequenceMatcher，阈值 0.85），相似查询直接命中
- LRU 淘汰 + 命中/未命中统计
- **时效性排序** — `freshness_half_life_days=30`，时效查询自动提升新鲜度

---

## 八、工具系统

### 8.1 ToolRegistry — 统一注册中心

`tools/core/registry.py` 特性：

- 动态注册/注销
- 模块自动发现
- 工具验证/测试
- **使用统计追踪**（call_count, success_count, average_duration_ms）
- 版本管理 + 废弃标记
- LangChain BaseTool 兼容

### 8.2 沙箱工具套件（E2B / Daytona）

`tools/sandbox/` 下有 **14 个沙箱工具**：

| 工具 | 功能 |
|---|---|
| `sandbox_shell_tool` | Shell 命令执行 |
| `sandbox_browser_tools` | 浏览器自动化 + 截图 |
| `sandbox_files_tool` | 文件 CRUD |
| `sandbox_web_search_tool` | 沙箱内搜索 |
| `sandbox_web_dev_tool` | Web 开发 |
| `sandbox_sheets_tool` | Excel/Sheets |
| `sandbox_presentation_tool` | PPT 生成 |
| `sandbox_presentation_tool_v2` | PPT v2 |
| `sandbox_presentation_outline_tool` | PPT 大纲 |
| `sandbox_image_edit_tool` | 图片编辑 |
| `sandbox_vision_tool` | 视觉理解 |
| `sandbox_browser_session` | 浏览器会话管理 |

### 8.3 浏览器自动化

`tools/browser/` 包含：

- `browser_tools.py` — Playwright 浏览器操作
- `browser_use_tool.py` — BrowserUse 集成
- `cdp_screencast.py` — CDP 屏幕录制
- `content_extractor.py` — 页面内容提取

### 8.4 其他工具

| 目录 | 工具 |
|---|---|
| `tools/code/` | Python 执行、chart_viz_tool 图表可视化 |
| `tools/crawl/` | Crawl4AI、通用爬虫 |
| `tools/export/` | Markdown → PDF/DOCX 转换 |
| `tools/rag/` | 文档加载、嵌入、ChromaDB 向量存储、RAG 检索 |
| `tools/io/` | ASR 语音识别、TTS 语音合成、截图服务 |
| `tools/automation/` | bash_tool、ask_human、computer_use、str_replace、task_list |
| `tools/planning/` | 规划工具 |
| `tools/mcp.py` | MCP SSE/Stdio 桥接 |

### 8.5 MCP 集成

`tools/mcp.py` + `tools/core/mcp_clients.py` 提供 MCP 桥接：

- 支持 SSE 和 Stdio 两种传输方式
- 多 server 并行连接
- 运行时热重载（`reload_mcp_tools`）
- 自动转换为 LangChain BaseTool

---

## 九、流式体验与事件系统

### 9.1 ToolEvent 实时事件

`agent/core/events.py` 定义 **21 种事件类型**：

- **工具生命周期**: `TOOL_START` → `TOOL_PROGRESS` → `TOOL_SCREENSHOT` → `TOOL_RESULT`
- **任务列表**: `TASK_CREATE` → `TASK_UPDATE` → `TASK_COMPLETE`
- **研究可视化**: `RESEARCH_NODE_START/COMPLETE`, `RESEARCH_TREE_UPDATE`, `SEARCH`, `QUALITY_UPDATE`
- **Agent 循环**: `AGENT_START` → `AGENT_ITERATION` → `AGENT_DONE`
- **内容流**: `CONTENT`, `THINKING`

### 9.2 SSE 协议

`common/sse.py` 提供：

- `format_sse_event()` — JSON data 单行编码
- `iter_with_sse_keepalive()` — 15 秒心跳保活
- `iter_abort_on_disconnect()` — 客户端断开检测 + 优雅停止

### 9.3 响应处理

`agent/workflows/response_handler.py` 支持：

- 流式响应处理
- 双模式工具调用检测（XML + Native）
- 可配置执行策略（sequential/parallel）
- 工具结果注入
- 自动续写机制（`agent/workflows/continuation.py`）

---

## 十、企业级特性

### 10.1 任务取消系统

`common/cancellation.py` 提供：

- 11 个预定义检查点（before/after LLM call, tool call, search, crawl, loop iteration, node entry/exit）
- `CancellationToken` + `CancellationManager` 全局管理
- 支持取消回调和资源清理

### 10.2 HITL（Human-in-the-Loop）

通过 `hitl_checkpoints` 配置四级审批：

- `plan` — 搜索计划审批
- `sources` — 来源列表审批
- `draft` — 报告草稿审批
- `final` — 最终报告审批

使用 LangGraph `interrupt()` 原语实现暂停/恢复。

### 10.3 并发控制

`common/concurrency.py` 中 `ConcurrencyController`：

- 信号量限制并发数
- API 速率限制
- 批量任务处理

### 10.4 Tracing

`common/tracing.py` 提供轻量级追踪：

- Span 树结构: Node → LLM Call → Tool Call
- 内存环形缓冲区
- 可选 OTLP 导出
- 装饰器集成

### 10.5 触发器系统

`triggers/` 提供三种触发器：

- **ScheduledTrigger** — Cron 定时任务
- **WebhookTrigger** — HTTP 端点触发
- **EventTrigger** — 内部事件触发

### 10.6 协作功能

`common/collaboration.py` 提供：

- 分享链接（可设过期时间和权限）
- 评论系统
- 版本历史

### 10.7 速率限制

Token bucket 算法（`common/config.py`）：

- 通用接口: 60/min
- 聊天接口: 20/min
- 最大 10K bucket 防内存溢出
- 生产环境自动启用

### 10.8 Prometheus 监控

`common/metrics.py` 提供 `RunMetricsRegistry`，跟踪每次运行的：

- 模型、路由、耗时
- 节点启动/完成计数
- 错误列表
- 取消状态

---

## 十一、前端 — Next.js 14

### 11.1 组件架构

| 目录 | 组件 |
|---|---|
| `web/components/chat/` | Chat, ChatInput, ChatInterface, MessageItem, SearchModeSelector, BrowserViewer, ArtifactsPanel, MermaidBlock |
| `web/components/ui/` | 17 个 shadcn/ui 风格组件（button, dialog, select, tooltip 等） |
| `web/hooks/` | useChatStream, useBrowserEvents, useBrowserStream, useChatHistory, useArtifacts |
| `web/lib/` | SSE 协议、i18n、存储服务、公开配置 |

### 11.2 关键特性

- **BrowserViewer** — 实时沙箱浏览器可视化
- **ArtifactsPanel** — 代码/文件产物面板
- **react-virtuoso** — 长输出虚拟滚动
- **Mermaid** 图表 + **KaTeX** 数学公式渲染
- **SSE 双协议** — `sse` 和 `legacy` 可切换
- **DataTableView** — CSV 数据表格展示
- **SearchModeSelector** — 搜索模式选择（direct/web/deep/agent）

---

## 十二、配置系统

`common/config.py` 中 `Settings` 类有 **150+ 配置项**，支持 `.env` + `config/config.toml` 双配置源：

| 类别 | 示例配置 |
|---|---|
| **模型** | `primary_model`, `reasoning_model`, `planner_model`, `writer_model`, `evaluator_model` |
| **搜索** | `search_strategy`, 10+ provider key, freshness ranking, cache TTL |
| **深度搜索** | `deepsearch_mode`, `max_epochs`, `query_num`, gap analysis, tree exploration |
| **沙箱** | `sandbox_mode` (local/daytona/none), E2B key, Daytona VNC |
| **工具** | `tool_retry`, `tool_call_limit`, XML/Native 双模式工具调用 |
| **HITL** | `hitl_checkpoints`, `human_review`, `tool_approval` |
| **RAG** | `rag_enabled`, `rag_store_path`, ChromaDB 配置 |
| **Memory** | `enable_memory`, mem0 配置, LangGraph Store backend |
| **限流** | `rate_limit_*`, 并发控制 |
| **Tracing** | `enable_tracing`, OTLP endpoint |
| **质量门禁** | `citation_gate_min_coverage`, `claim_verifier_gate_max_contradicted` |

---

## 十三、Docker 部署

`docker/docker-compose.yml` 定义四个服务：

- **postgres** — pgvector/pgvector:pg16，Checkpoint 持久化
- **redis** — redis-stack-server:7.4，缓存 + 长期记忆
- **backend** — FastAPI + uvicorn，热重载模式
- **frontend** — Next.js dev server，端口 3100

---

## 十四、亮点总结

| 亮点 | 说明 |
|---|---|
| **树状深度研究** | 主题分解→子主题并行探索→分支合并，支持深度/广度/预算控制 |
| **IterDRAG 知识缺口分析** | 每轮迭代后自动识别信息盲区，生成精准补充查询 |
| **12+ 搜索引擎聚合** | 通用搜索 + 社交媒体 Feed + 学术搜索，断路器 + 指数退避 |
| **事实验证 (ClaimVerifier)** | 从报告提取声明 → 匹配证据 → 方向性矛盾检测（增长 vs 下降） |
| **多模型阶段路由** | 规划用推理模型、研究用主力模型、评估用批评模型 |
| **领域专家路由** | 8 领域分类，自动适配搜索源和语言风格 |
| **HITL 4 级审批** | plan / sources / draft / final 4 个人工检查点，LangGraph interrupt |
| **SSE 流式事件系统** | 21 种事件类型，工具执行 + 研究树 + 质量指标实时可视化 |
| **知识压缩** | 搜索到 Writer 之间插入 Compressor，提取事实/统计/关键实体，去冗余 |
| **报告自动图表** | VizPlanner 从数据模式生成 Matplotlib 图表嵌入报告 |
| **搜索缓存 + 语义去重** | SequenceMatcher 模糊匹配相似查询，避免重复 API 调用 |
| **XML + Native 双模式工具调用** | 同时支持 Claude XML 格式和 OpenAI function calling |
| **OpenAPI 合约对齐** | 后端 OpenAPI → 前端 TS types 自动生成，防止接口漂移 |
| **完整取消系统** | 11 个检查点 + 取消令牌 + 清理回调，可优雅中止长时间研究 |
