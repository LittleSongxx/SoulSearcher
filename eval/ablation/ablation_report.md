# Deep Research 消融实验报告 (Ablation Study)

**日期**: 2026-04-22 (v3)  
**测试条件**: 快速模式 (max_epochs=2, tree_depth=1, branches=3, queries/branch=2, results/query=3, max_seconds=300, subprocess timeout=600s)  
**测试 Case**: 5 个代表性 case (financial, technical, legal, medical, scientific)  
**变体数**: 6 (full, no_iterdrag, no_claimverify, linear, single_provider, hierarchical)

---

## 1. 总览对比表

| 变体 | 完成率 | 平均耗时 | 平均报告长度 | 平均 Query Coverage | 平均引用源数 |
|---|---|---|---|---|---|
| **full (baseline)** | **5/5 (100%)** | 341s | 11,381 chars | 0.56 | 37 |
| no_iterdrag | 5/5 (100%) | 361s | 12,354 chars | 0.56 | 37 |
| no_claimverify | 5/5 (100%) | 278s | 11,990 chars | 0.48 | 35 |
| linear | 3/5 (60%) | 444s | 13,441 chars | 0.60 | 70 |
| single_provider | 5/5 (100%) | 336s | 10,812 chars | 0.64 | 35 |
| hierarchical | 3/5 (60%) | 346s | 4,548 chars | 0.00 | 5 |

> **注**: no_iterdrag 和 no_claimverify 在 v2 run 中因增加了 subprocess timeout (420s→600s) 而全部完成，v1 run 中分别有 2/1 个 timeout。

## 2. 各组件贡献分析

### 2.1 完整系统 (full) — Baseline
- **完成率 100%**，平均 341s/case，报告 ~11.4K chars
- Query Coverage 0.56，引用 37 个源
- 所有组件协同工作的最稳定配置

### 2.2 IterDRAG (知识空白分析)
- **关闭后**: 完成率 100%（增加 timeout 后），但平均耗时从 341s → 361s (**+5.9%**)
- 报告长度 12,354 chars (**+8.5%**) — 更长但不一定更好
- Query Coverage 持平 0.56
- **结论**: IterDRAG 主要贡献是**搜索效率**。关闭后系统仍能完成，但需要更长时间（因缺乏收敛引导）。在 v1 run (420s timeout) 中有 2 个 case 超时，说明 IterDRAG 在紧约束条件下对**稳定性**至关重要。

### 2.3 ClaimVerifier (声明验证)
- **关闭后**: 完成率 100%，耗时显著下降至 278s (**-18.5%**)
- Query Coverage 下降至 0.48 (**-14.3%**)
- 报告长度 11,990 chars (**+5.3%**)
- **结论**: ClaimVerifier 显著增加了处理时间（额外验证步骤），但提升了搜索质量 (QC +14.3%)。它是**质量-速度权衡**的关键组件。

### 2.4 Tree Explorer vs Linear Search
- **Linear 模式完成率 60%** (3/5)，2 个 case 在 600s timeout 内未完成
- 完成 case 的平均耗时 444s (**+30.2%** vs full)
- 报告长度 13,441 chars (**+18.1%**) — 更长的报告
- 引用源数 70 (**+89.2%**) — 单线程搜索获取了更多总源
- **根因分析**: Linear 模式不是 SSE 挂起，而是**线性搜索固有地慢于并行树搜索**。每个 query 串行执行，2 个 case 超过 600s 未完成。
- **结论**: Tree Explorer 的并行搜索架构对系统**效率**贡献极大（30%+ 速度提升），且提高完成率。Linear 模式在信息丰富度上有优势，但效率瓶颈明显。

### 2.5 Single Provider (仅 Tavily) vs Multi-Provider
- **完成率 100%**，与 full baseline 持平
- 报告长度 10,812 chars (**-5.0%**)
- Query Coverage 0.64 (**+14.3%**) — 略高，可能因搜索结果更一致
- 引用源数 35 (**-5.4%**)
- **结论**: 多搜索引擎聚合的主要贡献是**信息来源多样性**（+5% 源数量），而非搜索质量。单引擎在 coverage 上甚至略有优势（可能因 bocha/duckduckgo 结果质量不稳定导致噪声）。

### 2.6 Hierarchical Agents (协调者→规划→研究→汇报)
- **完成率 60%** (3/5)，2 个 case 超时
- 完成 case 的报告质量差异极大：case_005 产出 12,497 chars（与 baseline 相当），但 case_001 仅 1,125 chars，case_007 仅 23 chars
- 平均报告长度 4,548 chars (**-60.1%** vs full)
- **根因分析**: Hierarchical 模式的 coordinator 循环存在收敛问题（已修复无限循环 bug）。即使修复后，coordinator 的 LLM 决策不够稳定，有时过早选择 "complete" 导致报告过短。
- **结论**: Hierarchical 架构**尚未成熟**。当前实现需要进一步调优 coordinator 的决策策略（例如最低质量阈值、强制重搜条件等）才能在消融实验中产出有意义的对比数据。

## 3. 组件重要性排序

| 排名 | 组件 | 贡献维度 | 影响程度 |
|---|---|---|---|
| 1 | **Tree Explorer** | 效率 & 可靠性 | 🔴 关键 (30%+ 速度提升, 完成率 100%→60%) |
| 2 | **ClaimVerifier** | 搜索质量 | 🟠 重要 (QC +14.3%, 但增加 18% 耗时) |
| 3 | **IterDRAG** | 收敛效率 & 紧约束稳定性 | 🟡 中等 (耗时 +5.9%, 紧约束下完成率下降) |
| 4 | **Multi-Provider** | 信息源多样性 | 🟢 轻微 (报告长度 -5%, 源数 -5.4%) |
| 5 | **Hierarchical Agents** | 智能循环控制 | ⚪ 实验性 (当前实现质量不稳定) |

## 4. 发现的 Bug 及修复

### 4.1 Linear 模式 "SSE 挂起" (实为 timeout 不足)
- **症状**: v1 run 中所有 linear case 超时
- **根因**: Linear 模式串行执行所有搜索，固有地比 tree 慢；FAST_ENV 的 `DEEPSEARCH_MAX_SECONDS=300` 加上 LLM 调用超过了 420s subprocess timeout
- **修复**: 降低 linear 的 epoch/query 数量 (`MAX_EPOCHS=1`, `QUERY_NUM=3`)，增加 timeout (600s)

### 4.2 Hierarchical 模式无限循环
- **症状**: 所有 hierarchical case 超时或递归限制
- **根因**: `coordinator_node` 使用 `revision_count` 判断迭代次数，但该变量只在 evaluator 路径中递增；hierarchical 路径跳过 evaluator，导致 `revision_count` 永远为 0
- **修复**: 在 `coordinator_node` 中添加独立的 `coordinator_iterations` 计数器，超过 `max_revisions + 1` 次后强制 "complete"

## 5. 局限性与后续建议

1. **快速模式下参数缩减** 可能放大或缩小某些差异，全参数测试可提供更精确数据
2. **ClaimVerifier 的真正价值** 体现在报告中无支撑声明 (unsupported claims) 的数量上，需要额外指标来量化
3. **Search Cache** 未单独测试（本次所有变体共享相同 cache 设置），建议后续添加 no_cache 变体
4. 每变体仅 5 个 case，样本量较小，结论需审慎解读
5. **Hierarchical 模式** 需进一步优化 coordinator 决策逻辑后重测
6. **Linear 模式** 仍有 2/5 超时，建议进一步缩小搜索范围或增加 timeout

## 6. Per-Case 明细

### full (baseline)
| Case | Domain | Time | Chars | QC Score | Sources |
|---|---|---|---|---|---|
| case_001 | financial | 356s | 11,722 | 0.40 | 36 |
| case_004 | technical | 332s | 11,070 | 0.60 | 35 |
| case_005 | legal | 341s | 10,755 | 0.80 | 36 |
| case_007 | medical | 346s | 11,385 | 0.40 | 39 |
| case_010 | scientific | 330s | 11,975 | 0.60 | 39 |

### no_iterdrag
| Case | Domain | Time | Chars | Status |
|---|---|---|---|---|
| case_001 | financial | 419s | 11,448 | ✅ |
| case_004 | technical | 424s | 12,689 | ✅ |
| case_005 | legal | 334s | 12,765 | ✅ |
| case_007 | medical | 303s | 11,154 | ✅ |
| case_010 | scientific | 325s | 13,713 | ✅ |

### no_claimverify
| Case | Domain | Time | Chars | Status |
|---|---|---|---|---|
| case_001 | financial | 322s | 12,283 | ✅ |
| case_004 | technical | 286s | 10,090 | ✅ |
| case_005 | legal | 330s | 12,565 | ✅ |
| case_007 | medical | 292s | 11,836 | ✅ |
| case_010 | scientific | 160s | 13,177 | ✅ |

### linear
| Case | Domain | Time | Chars | Status |
|---|---|---|---|---|
| case_001 | financial | 600s | 0 | ❌ timeout |
| case_004 | technical | 422s | 12,794 | ✅ |
| case_005 | legal | 465s | 14,375 | ✅ |
| case_007 | medical | 444s | 13,155 | ✅ |
| case_010 | scientific | 600s | 0 | ❌ timeout |

### single_provider (Tavily only)
| Case | Domain | Time | Chars | QC Score | Sources |
|---|---|---|---|---|---|
| case_001 | financial | 354s | 10,573 | 0.60 | 31 |
| case_004 | technical | 339s | 11,062 | 0.60 | 36 |
| case_005 | legal | 331s | 11,181 | 0.80 | 36 |
| case_007 | medical | 318s | 9,242 | 0.40 | 31 |
| case_010 | scientific | 336s | 12,003 | 0.80 | 39 |

### hierarchical
| Case | Domain | Time | Chars | Status |
|---|---|---|---|---|
| case_001 | financial | 303s | 1,125 | ✅ (短报告) |
| case_004 | technical | 600s | 0 | ❌ timeout |
| case_005 | legal | 346s | 12,497 | ✅ |
| case_007 | medical | 389s | 23 | ✅ (极短报告) |
| case_010 | scientific | 600s | 0 | ❌ timeout |
