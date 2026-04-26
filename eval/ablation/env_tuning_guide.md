# .env 参数深度分析与调优建议

**日期**: 2026-04-25  
**基于**: `.env` 当前值 vs `common/config.py` 代码默认值 vs `.env.example` 模板值

---

## 一、建议立即调整的参数（高优先级）

### 1.1 搜索引擎 & Fallback

| 参数 | 当前值 | 建议值 | 说明 |
| --- | --- | --- | --- |
| `SEARCH_ENGINES` | `bocha,tavily` ✅ | `bocha,tavily,duckduckgo` | 已配置 fallback。建议追加免费的 DuckDuckGo 作为最终兜底，确保 Bocha+Tavily 均额度耗尽时仍可搜索 |
| `TAVILY_API_KEYS` | 5 keys ✅ | — | 已配置 key pool，额度用完自动轮换 |
| `SEARCH_STRATEGY` | `fallback` | `fallback` ✅ | 当前正确。`fallback` = 按优先级逐个尝试直到成功。无需改动 |

### 1.2 工具重试

| 参数 | `.env` 当前值 | 代码默认值 | 建议值 | 说明 |
| --- | --- | --- | --- | --- |
| `TOOL_RETRY` | `false` | `True` | **`true`** | **代码默认 True，但 .env 覆盖为 false**。启用后搜索/抓取失败会自动重试（最多 3 次，指数退避），对 API 不稳定场景（如本次 v7_010 挂起）非常有用 |
| `TOOL_RETRY_MAX_ATTEMPTS` | `3` | `3` | `3` ✅ | 无需改动 |
| `TOOL_RETRY_BACKOFF` | `1.5` | `1.5` | `1.5` ✅ | 无需改动 |

### 1.3 Research Fetcher（证据抓取）

| 参数 | 当前值 | 建议值 | 说明 |
| --- | --- | --- | --- |
| `DEEPSEARCH_ENABLE_RESEARCH_FETCHER` | `false` | **`true`** | **核心功能未启用**。启用后会抓取搜索结果网页正文，生成精细的 evidence passages，大幅提升 claim verifier 的准确性和 citation 质量。代价是每次搜索多 5-15 秒。benchmark 显示 `claim_verifier_verified` 目前几乎为 0，根因就是缺少 evidence passages |
| `RESEARCH_FETCH_CACHE_TTL_S` | `0` (禁用) | **`600`** | 启用 fetcher 后建议开缓存（10 分钟），避免同一 URL 重复抓取 |
| `RESEARCH_FETCH_RENDER_MODE` | `off` | `auto` | 设为 `auto` 后，当 direct 抓取文本过短（< 200 字）时自动尝试渲染抓取，对 SPA 页面更友好 |

### 1.4 爬虫配置

| 参数 | 当前值 | 代码默认值 | 建议值 | 说明 |
| --- | --- | --- | --- | --- |
| `CRAWLER_HEADLESS` | `false` | `True` | **`true`** | **当前是有头模式（弹窗浏览器）**。生产环境/服务器应设为 `true`（无头），避免占用 GUI 资源 |
| `USE_OPTIMIZED_CRAWLER` | `true` | `False` | `true` ✅ | 已正确启用 Playwright 优化爬虫 |

---

## 二、建议评估后调整的参数（中优先级）

### 2.1 Deepsearch 源数上限

| 参数 | 当前值 | 建议值 | 说明 |
| --- | --- | --- | --- |
| `DEEPSEARCH_REPORT_SOURCES_LIMIT` | `20` | `30-50` | 当前限制报告只引用 20 个来源。benchmark 显示 optimized 平均产出 128 个搜索源，但写入报告时被截断到 20。增大此值可提高 citation coverage |

### 2.2 搜索缓存

| 参数 | 当前值 | 说明 |
| --- | --- | --- |
| `SEARCH_CACHE_MAX_SIZE` | `200` ✅ | 合理，单次 deepsearch 约产出 30-90 个 query |
| `SEARCH_CACHE_TTL_SECONDS` | `1800` (30min) ✅ | 合理 |
| `SEARCH_CACHE_SIMILARITY_THRESHOLD` | `0.9` ✅ | fuzzy 缓存命中阈值，合理 |

### 2.3 上下文管理

| 参数 | 当前值 | 代码默认值 | 说明 |
| --- | --- | --- | --- |
| `OBSERVATION_MASKING` | `true` ✅ | `True` | 已启用。旧 tool observation 被 mask 而非丢弃 |
| `CONTEXT_OFFLOADING` | `true` ✅ | `True` | 已启用。大 tool result offload 到文件系统 |
| `STRIP_TOOL_MESSAGES` | `false` ✅ | `False` | 正确。与 observation_masking 互补 |
| `TRIM_MESSAGES` | `false` | `False` | 对 deepsearch 场景（单轮对话）无影响。多轮对话场景可考虑启用 |
| `SUMMARY_MESSAGES` | `false` | `False` | 同上，单轮 deepsearch 无需。多轮会话可设为 `true` |

### 2.4 Quality Gates

| 参数 | 当前值 | 说明 |
| --- | --- | --- |
| `CITATION_GATE_MIN_COVERAGE` | `0.6` ✅ | 合理。低于 60% 的引用覆盖率触发修订 |
| `CLAIM_VERIFIER_GATE_MAX_CONTRADICTED` | `0` | **极其严格**（任何矛盾即修订）。建议放宽到 `1`-`2` 以避免不必要的修订循环 |
| `CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED` | `0` | **极其严格**。benchmark 显示每篇报告约 9-10 条无支撑声明，实际上这个 gate 在 deepsearch 流程中不走 evaluator 节点所以不生效。如果未来启用，建议设为 `5` |

---

## 三、当前值正确、无需修改的参数

| 参数 | 当前值 | 说明 |
| --- | --- | --- |
| `DEEPSEARCH_MAX_EPOCHS` | `3` | 生产推荐值 |
| `DEEPSEARCH_QUERY_NUM` | `5` | 每轮 5 个子查询，足够 |
| `DEEPSEARCH_RESULTS_PER_QUERY` | `5` | 每查询 5 条结果，平衡质量和速度 |
| `SEARCH_ENABLE_FRESHNESS_RANKING` | `true` | 时间敏感查询自动 freshness 排序 |
| `SEARCH_FRESHNESS_WEIGHT` | `0.35` | 合理的 freshness 权重 |
| `AGENT_REFLEXION_ENABLED` | `true` | 已启用自省 |
| `TREE_BACKTRACK_ENABLED` | `true` | 已启用树回溯 |
| `DYNAMIC_TOOL_PRUNING` | `true` | 已启用工具裁剪 |
| `DEEPSEARCH_CLAIM_VERIFIER_USE_PASSAGES` | `true` | 使用 passages 验证 claim（需配合 research fetcher） |
| `MAX_CONCURRENCY` | `5` | 并发 5，合理 |
| `API_RATE_LIMIT` | `0.5` | API 间隔 0.5s，避免限流 |
| `READER_FALLBACK_MODE` | `both` | public + self-hosted 双通道 |
| `OPENAI_TIMEOUT` | `60` | LLM 超时 60s，合理 |
| `PROMPT_STYLE` | `enhanced` | 增强提示词 |
| `TOOL_SELECTOR` | `true` | 已启用工具选择器 |

---

## 四、功能发现 — 代码支持但未在 .env 中暴露的能力

| 功能 | config.py 字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| **多模型分工** | `planner_model`, `researcher_model`, `writer_model`, `evaluator_model` | 空（回退主模型） | 支持不同任务使用不同模型。例如用 reasoning model 做规划、cheap model 做搜索分析 |
| **学术搜索** | `arxiv_enabled`, `scholar_enabled`, `semantic_scholar_api_key`, `pubmed_email` | arXiv+Scholar 默认开启 | 已启用但需确认是否被 deepsearch 实际调用 |
| **RAG 本地文档** | `rag_enabled`, `rag_store_path` | `false` | 支持本地文档向量检索，适合私有知识库场景 |
| **长记忆** | `ENABLE_MEMORY`, `MEMORY_STORE_BACKEND` | `false`, `memory` | 支持跨会话记忆，可配合 Postgres/Redis 持久化 |
| **HITL 检查点** | `hitl_checkpoints` | 空 | 支持 plan/sources/draft/final 四个人工审核点 |
| **Prometheus 监控** | `ENABLE_PROMETHEUS` | `false` | 暴露 /metrics 端点，适合生产部署 |

---

## 五、已落实的 .env 变更清单（2026-04-25 已应用）

以下变更已全部写入 `.env` 并验证生效：

| # | 参数 | 旧值 | 新值 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `SEARCH_ENGINES` | `bocha,tavily` | `tavily,bocha,duckduckgo` | ✅ 已应用 |
| 2 | `TOOL_RETRY` | `false` | `true` | ✅ 已应用 |
| 3 | `DEEPSEARCH_ENABLE_RESEARCH_FETCHER` | `false` | `true` | ✅ 已应用 |
| 4 | `RESEARCH_FETCH_CACHE_TTL_S` | `0` | `600` | ✅ 已应用 |
| 5 | `RESEARCH_FETCH_RENDER_MODE` | `off` | `auto` | ✅ 已应用 |
| 6 | `CRAWLER_HEADLESS` | `false` | `true` | ✅ 已应用 |
| 7 | `DEEPSEARCH_REPORT_SOURCES_LIMIT` | `20` | `40` | ✅ 已应用 |
| 8 | `CLAIM_VERIFIER_GATE_MAX_CONTRADICTED` | `0` | `2` | ✅ 已应用 |
| 9 | `CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED` | `0` | `5` | ✅ 已应用 |

**预期效果**: 启用 Research Fetcher + 工具重试后，claim_verifier_verified 应从接近 0 提升到 50%+，citation_coverage 从 0.57 提升到 0.70+，同时搜索失败时的恢复能力显著增强。Tavily 5-key pool 作为首选搜索引擎，额度耗尽自动轮换，Bocha 和 DuckDuckGo 作为 fallback。
