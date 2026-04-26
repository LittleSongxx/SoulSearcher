# v9 生产级 Benchmark 报告：Baseline vs Optimized（当前 .env 配置）

**日期**: 2026-04-26 15:25  
**分支**: dev2  
**模型**: deepseek-v4-flash（两组相同）  
**参数**: 当前 .env 配置 + baseline/optimized 特性开关  
**超时**: 每 case 900s 硬上限；报告中保留事后阈值完成率  
**测试集**: v7_tasks.jsonl — 与 v7/v8 相同的 10 case（5 领域 × 2）  
**Judge**: 当前 PRIMARY_MODEL, 完整报告, 每 case 3 次评分取平均

### v9 当前 .env 参数快照

| 参数 | 值 |
|---|---|
| PRIMARY_MODEL | deepseek-v4-flash |
| REASONING_MODEL | deepseek-v4-pro |
| SEARCH_ENGINES | tavily,bocha |
| SEARCH_STRATEGY | fallback |
| TOOL_RETRY | false |
| TOOL_CALL_LIMIT | 12 |
| DEEPSEARCH_MAX_EPOCHS | 3 |
| DEEPSEARCH_QUERY_NUM | 5 |
| DEEPSEARCH_RESULTS_PER_QUERY | 5 |
| DEEPSEARCH_REPORT_SOURCES_LIMIT | 30 |
| DEEPSEARCH_ENABLE_CRAWLER | false |
| DEEPSEARCH_ENABLE_RESEARCH_FETCHER | true |
| RESEARCH_FETCH_TIMEOUT_S | 10 |
| RESEARCH_FETCH_CONCURRENCY | 10 |
| RESEARCH_FETCH_CACHE_TTL_S | 0 |
| RESEARCH_FETCH_RENDER_MODE | off |
| CRAWLER_HEADLESS | false |
| CLAIM_VERIFIER_GATE_MAX_CONTRADICTED | 0 |
| CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED | 0 |

---

## 一、总览对比表

### 1.1 运行指标

| 指标 | Baseline | Optimized | 变化 |
|---|---|---|---|
| **完成率** | 10/10 | 10/10 | — |
| **平均耗时** | 323s | 352s | +8.7% ⬆️ |
| **报告长度** | 12,922 chars | 12,645 chars | -2.1% ⬇️ |
| **Query Coverage** | 0.580 | 0.620 | +6.9% ⬆️ |
| **引用源数** | 115.4 | 124.2 | +7.6% ⬆️ |
| **无支撑声明数 (UC)** | 9.4 | 9.6 | +2.1% ⬆️ |
| **30天新鲜度** | 0.000 | 0.002 | N/A |
| **引用覆盖率** | 0.660 | 0.592 | -10.3% ⬇️ |
| **Claim Verifier total** | 10.0 | 10.0 | +0.0% — |
| **Claim Verifier verified** | 0.6 | 0.4 | -33.3% ⬇️ |

### 1.2 LLM-as-Judge 评分（完整报告 × 3 次平均）

| 维度 | Baseline (N=10) | Optimized (N=10) | 变化 |
|---|---|---|---|
| **Coverage** | 9.13 | 9.17 | +0.4% ⬆️ |
| **Depth** | 8.63 | 8.53 | -1.2% ⬇️ |
| **Structure** | 9.46 | 9.37 | -1.0% ⬇️ |
| **Citations** | 7.80 | 7.07 | -9.4% ⬇️ |
| **Overall** | 8.56 | 8.10 | -5.4% ⬇️ |

### 1.3 事后完成率（不同耗时阈值）

| 阈值 | Baseline | Optimized |
|---|---|---|
| 600s (10min) | 10/10 | 9/10 |
| 900s (15min) | 10/10 | 9/10 |
| 1200s (20min) | 10/10 | 10/10 |
| 1800s (30min) | 10/10 | 10/10 |

---

## 二、分领域对比

### Financial

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 319s | 304s |
| 报告长度 | 11851 | 12512 |
| J-Overall | 8.66 | 8.00 |
| J-Citations | 8.16 | 6.83 |

### Legal

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 327s | 630s |
| 报告长度 | 11702 | 13216 |
| J-Overall | 7.67 | 8.34 |
| J-Citations | 6.17 | 7.50 |

### Medical

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 351s | 258s |
| 报告长度 | 13704 | 11063 |
| J-Overall | 8.83 | 7.83 |
| J-Citations | 8.16 | 6.83 |

### Scientific

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 306s | 227s |
| 报告长度 | 12836 | 13152 |
| J-Overall | 8.33 | 7.00 |
| J-Citations | 7.50 | 5.17 |

### Technical

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 314s | 340s |
| 报告长度 | 14520 | 13280 |
| J-Overall | 9.34 | 9.33 |
| J-Citations | 9.00 | 9.00 |

---

## 三、逐 Case 详情

| Case | Domain | Variant | 耗时 | 长度 | QC | Src | UC | CitCov | J-Overall |
|---|---|---|---|---|---|---|---|---|---|
| v7_001 | financial | baseline | 304s | 10,886 | 0.80 | 122 | 10 | 0.692 | 9.0 |
| v7_002 | financial | baseline | 333s | 12,816 | 0.80 | 137 | 9 | 0.685 | 8.33 |
| v7_003 | technical | baseline | 327s | 16,895 | 0.60 | 119 | 10 | 0.576 | 9.67 |
| v7_004 | technical | baseline | 302s | 12,146 | 0.40 | 102 | 10 | 0.793 | 9.0 |
| v7_005 | legal | baseline | 340s | 10,660 | 0.80 | 112 | 9 | 0.760 | 7.33 |
| v7_006 | legal | baseline | 314s | 12,744 | 0.60 | 108 | 9 | 0.879 | 8.0 |
| v7_007 | medical | baseline | 368s | 13,467 | 0.40 | 113 | 10 | 0.677 | 8.33 |
| v7_008 | medical | baseline | 334s | 13,940 | 0.60 | 105 | 8 | 0.659 | 9.33 |
| v7_009 | scientific | baseline | 299s | 11,119 | 0.60 | 133 | 9 | 0.372 | 8.33 |
| v7_010 | scientific | baseline | 314s | 14,552 | 0.20 | 103 | 10 | 0.510 | 8.33 |
| v7_001 | financial | optimized | 307s | 12,321 | 0.60 | 117 | 8 | 0.625 | 7.67 |
| v7_002 | financial | optimized | 300s | 12,704 | 0.60 | 129 | 10 | 0.457 | 8.33 |
| v7_003 | technical | optimized | 373s | 13,200 | 0.60 | 102 | 10 | 0.938 | 9.33 |
| v7_004 | technical | optimized | 306s | 13,361 | 0.60 | 122 | 10 | 0.844 | 9.33 |
| v7_005 | legal | optimized | 918s | 11,191 | 0.60 | 131 | 10 | 0.618 | 7.67 |
| v7_006 | legal | optimized | 343s | 15,241 | 0.80 | 112 | 10 | 0.500 | 9.0 |
| v7_007 | medical | optimized | 287s | 9,931 | 0.60 | 122 | 9 | 0.250 | 8.0 |
| v7_008 | medical | optimized | 229s | 12,195 | 0.40 | 135 | 9 | 0.632 | 7.67 |
| v7_009 | scientific | optimized | 247s | 14,584 | 0.80 | 137 | 10 | 0.600 | 8.0 |
| v7_010 | scientific | optimized | 207s | 11,720 | 0.60 | 135 | 10 | 0.455 | 6.0 |

---

## 四、v7/v8/v9 横向回顾

| Version | Variant | 完成率 | 平均耗时 | Src | CitCov | J-Overall | J-Citations |
|---|---|---|---|---|---|---|---|
| v7 | baseline | 10/10 | 176s | 69.0 | 0.410 | 6.40 | 4.77 |
| v7 | optimized | 10/10 | 153s | 128.1 | 0.566 | 7.27 | 6.00 |
| v8 | baseline | 10/10 | 222s | 115.9 | 0.589 | 7.60 | 6.40 |
| v8 | optimized | 10/10 | 246s | 121.2 | 0.613 | 7.53 | 6.63 |
| v9 | baseline | 10/10 | 323s | 115.4 | 0.660 | 8.56 | 7.80 |
| v9 | optimized | 10/10 | 352s | 124.2 | 0.592 | 8.10 | 7.07 |

---

## 附录：测试环境

| 项目 | 值 |
|---|---|
| 分支 | dev2 |
| 模型 | deepseek-v4-flash |
| 参数 | 当前 .env 配置 + baseline/optimized 特性开关 |
| 超时 | 每 case 900s 硬上限；事后阈值判定 |
| 测试集 | eval/benchmarks/v7_tasks.jsonl |
| Judge | 当前 PRIMARY_MODEL, 完整报告, 3 次评分取平均 |
| 数据文件 | v9_baseline.json, v9_optimized.json, v9_judge_scores.json |
