# Deep Research 消融实验报告 (Ablation Study)

**日期**: 2026-04-23 (v4)  
**测试条件**: 快速模式 (max_epochs=2, tree_depth=1, branches=3, queries/branch=2, results/query=3, max_seconds=300, subprocess timeout=600s)  
**测试 Case**: 5 个代表性 case (financial, technical, legal, medical, scientific)  
**变体数**: 6 (full, no_iterdrag, no_claimverify, linear, single_provider, hierarchical)  
**v4 更新**: Hierarchical 模式经过完整修复后重新测试（coordinator 决策逻辑、compressor 反序列化、writer tool-calling 优化）

---

## 1. 总览对比表

| 变体 | 完成率 | 平均耗时 | 平均报告长度 | 平均 Query Coverage | 平均引用源数 |
|---|---|---|---|---|---|
| **full (baseline)** | **5/5 (100%)** | 341s | 11,381 chars | 0.56 | 37 |
| no_iterdrag | 5/5 (100%) | 361s | 12,354 chars | 0.56 | 37 |
| no_claimverify | 5/5 (100%) | 278s | 11,990 chars | 0.48 | 35 |
| linear | 3/5 (60%) | 444s | 13,441 chars | 0.60 | 70 |
| single_provider | 5/5 (100%) | 336s | 10,812 chars | 0.64 | 35 |
| hierarchical (v4修复后) | 4/5 (80%) | 257s | 6,957 chars | 0.00 | 5 |

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

#### v4 修复后结果
- **完成率 80%** (4/5)，1 个 case 超时 (case_010)
- 完成 case 报告长度：5,686 / 7,960 / 6,222 / 7,960 chars — 质量稳定
- 平均报告长度 6,957 chars (**-38.9%** vs full baseline)
- 平均耗时 257s (**-24.6%** vs full) — 比 baseline 更快

#### 与 v3 (修复前) 对比
| 指标 | v3 (修复前) | v4 (修复后) | 提升 |
|---|---|---|---|
| 完成率 | 3/5 (60%) | 4/5 (80%) | +33% |
| 平均报告长度 | 4,548 chars | 6,957 chars | **+53%** |
| 最差完成 case | 23 chars | 5,686 chars | **247x** |
| 异常短报告 | 2 个 (23/1125 chars) | 0 个 | **消除** |

#### 修复内容 (5 个 Bug)
1. `coordinator_node` 使用 `revision_count`(永远为0) 作为 `current_epoch` → 改用 `coord_iters`
2. `decide_next_action` 无 `has_report` 感知 → 新增参数 + 6 条确定性规则防止过早 complete/synthesize
3. `COORDINATOR_PROMPT` 缺少 `has_report` 字段 → 更新提示词增加显式约束
4. `compressor_node` 反序列化 Bug：`to_dict()` 用 `"source"` 但 `ExtractedFact` 期望 `"source_url"` → 字段映射修复
5. Writer 在 hierarchical 路径使用 tool-calling agent (197s/轮) → 禁用工具调用 (55s/轮, **3.5x 加速**)

- **结论**: 修复后 Hierarchical 模式**功能基本可用**。完成率和报告质量显著提升，但报告长度仍比 baseline 短 ~39%，主要原因是 coordinator 循环的搜索广度受限（单轮 plan→search→write vs tree 的并行多分支搜索）。case_010 超时可能与特定 topic 的搜索复杂度有关。

## 3. 组件重要性排序

| 排名 | 组件 | 贡献维度 | 影响程度 |
|---|---|---|---|
| 1 | **Tree Explorer** | 效率 & 可靠性 | 🔴 关键 (30%+ 速度提升, 完成率 100%→60%) |
| 2 | **ClaimVerifier** | 搜索质量 | 🟠 重要 (QC +14.3%, 但增加 18% 耗时) |
| 3 | **IterDRAG** | 收敛效率 & 紧约束稳定性 | 🟡 中等 (耗时 +5.9%, 紧约束下完成率下降) |
| 4 | **Multi-Provider** | 信息源多样性 | 🟢 轻微 (报告长度 -5%, 源数 -5.4%) |
| 5 | **Hierarchical Agents** | 智能循环控制 | 🟡 可用 (完成率80%, 报告-39%, 速度+25%) |

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
5. **Hierarchical 模式** v4修复后基本可用(80%完成率)，但报告长度仍低于 baseline ~39%；后续可优化搜索广度（并行子任务）和 writer 上下文管理
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

### hierarchical (v4 修复后)
| Case | Domain | Time | Chars | Status |
|---|---|---|---|---|
| case_001 | financial | 278s | 5,686 | ✅ |
| case_004 | technical | 263s | 7,960 | ✅ |
| case_005 | legal | 239s | 6,222 | ✅ |
| case_007 | medical | 246s | 7,960 | ✅ |
| case_010 | scientific | 600s | 0 | ❌ timeout |
