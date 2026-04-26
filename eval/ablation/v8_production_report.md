# v8 生产级 Benchmark 报告：Baseline vs Optimized（新 .env 调优）

**日期**: 2026-04-25 15:28  
**分支**: dev2  
**模型**: deepseek-v4-flash（两组相同）  
**参数**: 生产默认值 (epochs=3, depth=2, branches=4, queries/branch=3, results/query=5, max_searches=30)  
**超时**: 无（事后阈值判定完成率）  
**测试集**: v7_tasks.jsonl — 与 v7 相同的 10 case（5 领域 × 2）  
**Judge**: deepseek-v4-flash, 完整报告, 每 case 3 次评分取平均

### .env 调优变化（v7 → v8）

| 参数 | v7 | v8 |
|---|---|---|
| SEARCH_ENGINES | bocha | tavily,bocha,duckduckgo |
| TOOL_RETRY | false | true |
| DEEPSEARCH_ENABLE_RESEARCH_FETCHER | false | true |
| RESEARCH_FETCH_CACHE_TTL_S | 0 | 600 |
| RESEARCH_FETCH_RENDER_MODE | off | auto |
| CRAWLER_HEADLESS | false | true |
| DEEPSEARCH_REPORT_SOURCES_LIMIT | 20 | 40 |
| CLAIM_VERIFIER_GATE_MAX_CONTRADICTED | 0 | 2 |
| CLAIM_VERIFIER_GATE_MAX_UNSUPPORTED | 0 | 5 |

---

## 一、总览对比表（13 项指标）

### 1.1 运行指标

| 指标 | Baseline | Optimized | 变化 |
|---|---|---|---|
| **完成率** | 10/10 | 10/10 | — |
| **平均耗时** | 222s | 246s | +11.1% ⬆️ |
| **报告长度** | 12,509 chars | 13,520 chars | +8.1% ⬆️ |
| **Query Coverage** | 0.740 | 0.660 | -10.8% ⬇️ |
| **引用源数** | 115.9 | 121.2 | +4.6% ⬆️ |
| **无支撑声明数 (UC)** | 9.8 | 9.8 | +0.0% — |
| **30天新鲜度** | 0.000 | 0.000 | N/A |
| **引用覆盖率** | 0.589 | 0.613 | +4.1% ⬆️ |

### 1.2 LLM-as-Judge 评分（完整报告 × 3 次平均）

| 维度 | Baseline (N=10) | Optimized (N=10) | 变化 |
|---|---|---|---|
| **Coverage** | 8.20 | 8.43 | +2.8% ⬆️ |
| **Depth** | 7.47 | 7.50 | +0.4% ⬆️ |
| **Structure** | 9.00 | 9.07 | +0.8% ⬆️ |
| **Citations** | 6.40 | 6.63 | +3.6% ⬆️ |
| **Overall** | 7.60 | 7.53 | -0.9% ⬇️ |

### 1.3 事后完成率（不同耗时阈值）

| 阈值 | Baseline | Optimized |
|---|---|---|
| 600s (10min) | 10/10 | 10/10 |
| 900s (15min) | 10/10 | 10/10 |
| 1200s (20min) | 10/10 | 10/10 |
| 1800s (30min) | 10/10 | 10/10 |

---

## 二、分领域对比

### Financial

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 209s | 214s |
| 报告长度 | 11686 | 12627 |
| J-Overall | 7.50 | 7.67 |
| J-Citations | 6.50 | 6.67 |

### Legal

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 232s | 306s |
| 报告长度 | 12215 | 17058 |
| J-Overall | 7.83 | 7.83 |
| J-Citations | 6.50 | 7.00 |

### Medical

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 226s | 222s |
| 报告长度 | 12660 | 11590 |
| J-Overall | 7.67 | 7.00 |
| J-Citations | 6.67 | 6.00 |

### Scientific

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 209s | 268s |
| 报告长度 | 11534 | 13383 |
| J-Overall | 7.50 | 7.67 |
| J-Citations | 6.00 | 6.67 |

### Technical

| 指标 | Baseline (N=2) | Optimized (N=2) |
|---|---|---|
| 平均耗时 | 234s | 222s |
| 报告长度 | 14450 | 12942 |
| J-Overall | 7.50 | 7.50 |
| J-Citations | 6.33 | 6.83 |

---

## 三、逐 Case 详情

| Case | Domain | Variant | 耗时 | 长度 | QC | Src | UC | J-Overall |
|---|---|---|---|---|---|---|---|---|
| v7_001 | financial | baseline | 212s | 12,064 | 0.80 | 122 | 10 | 8.0 |
| v7_002 | financial | baseline | 206s | 11,307 | 0.80 | 122 | 10 | 7.0 |
| v7_003 | technical | baseline | 240s | 14,807 | 0.80 | 102 | 10 | 8.0 |
| v7_004 | technical | baseline | 227s | 14,093 | 0.80 | 107 | 9 | 7.0 |
| v7_005 | legal | baseline | 231s | 11,770 | 0.80 | 137 | 10 | 7.67 |
| v7_006 | legal | baseline | 232s | 12,660 | 0.80 | 112 | 10 | 8.0 |
| v7_007 | medical | baseline | 220s | 12,714 | 0.60 | 93 | 9 | 7.33 |
| v7_008 | medical | baseline | 232s | 12,607 | 0.80 | 128 | 10 | 8.0 |
| v7_009 | scientific | baseline | 206s | 10,339 | 0.80 | 107 | 10 | 7.0 |
| v7_010 | scientific | baseline | 213s | 12,728 | 0.40 | 129 | 10 | 8.0 |
| v7_001 | financial | optimized | 220s | 12,004 | 0.80 | 141 | 10 | 7.33 |
| v7_002 | financial | optimized | 209s | 13,250 | 0.80 | 130 | 10 | 8.0 |
| v7_003 | technical | optimized | 233s | 14,751 | 0.40 | 107 | 9 | 8.0 |
| v7_004 | technical | optimized | 212s | 11,132 | 0.60 | 119 | 10 | 7.0 |
| v7_005 | legal | optimized | 223s | 15,373 | 0.60 | 126 | 10 | 7.0 |
| v7_006 | legal | optimized | 388s | 18,742 | 0.80 | 119 | 10 | 8.67 |
| v7_007 | medical | optimized | 209s | 11,464 | 0.60 | 103 | 9 | 7.0 |
| v7_008 | medical | optimized | 236s | 11,716 | 0.60 | 121 | 10 | 7.0 |
| v7_009 | scientific | optimized | 315s | 14,609 | 0.80 | 113 | 10 | 7.33 |
| v7_010 | scientific | optimized | 220s | 12,157 | 0.60 | 133 | 10 | 8.0 |

---

## 附录：测试环境

| 项目 | 值 |
|---|---|
| 分支 | dev2 |
| 模型 | deepseek-v4-flash |
| 参数 | epochs=3, depth=2, branches=4, queries/branch=3, results/query=5, max_searches=30 |
| 超时 | 无（事后阈值判定） |
| 测试集 | eval/benchmarks/v7_tasks.jsonl |
| Judge | deepseek-v4-flash, 完整报告, 3 次评分取平均 |
| 数据文件 | v8_baseline.json, v8_optimized.json, v8_judge_scores.json |
