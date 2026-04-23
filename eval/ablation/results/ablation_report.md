# Ablation: full vs hybrid (轻量版对比)

**日期**: 2026-04-23  
**配置**: FAST_ENV (depth=1, branches=3, epochs=2, max_searches=15)  
**Cases**: 5 (case_001, 004, 005, 007, 010)

## 执行结果

| Case | full (status / time) | hybrid (status / time) |
|---|---|---|
| case_001 | timeout / 900s | **completed / 287s** |
| case_004 | completed / 185s | completed / 331s |
| case_005 | completed / 173s | timeout / 900s |
| case_007 | completed / 176s | timeout / 900s |
| case_010 | completed / 162s | completed / 281s |
| **完成率** | **4/5** | **3/5** |

## 性能指标（仅计完成 case）

| 指标 | full (N=4) | hybrid (N=3) | 差异 |
|---|---|---|---|
| 平均耗时 | 174s | 300s | +72% |
| 平均报告长度 | 12,154 chars | 12,519 chars | +3% |
| 平均来源数 | 38 | 52 | +37% |
| 查询覆盖率 (QC) | 0.50 | 0.60 | +20% |
| 引用覆盖率 | n/a | 0.68 | *(baseline 未采集)* |
| 事实验证率 | n/a | 0% | *(baseline 未采集)* |

## LLM-as-Judge 评分 (0-10)

| 维度 | full (N=4) | hybrid (N=3) | 差异 |
|---|---|---|---|
| Coverage (覆盖度) | 6.8 | **7.3** | **+0.5** |
| Depth (深度) | 5.8 | **6.3** | **+0.5** |
| Structure (结构) | 7.8 | **8.3** | **+0.5** |
| Citations (引用) | 2.5 | 2.3 | -0.2 |
| **Overall (综合)** | **5.8** | **6.3** | **+0.5** |

## 逐 case LLM-Judge 评分

### full
| Case | C | D | S | Ci | O |
|---|---|---|---|---|---|
| case_004 | 7 | 6 | 8 | 2 | 6 |
| case_005 | 7 | 6 | 8 | 2 | 6 |
| case_007 | 6 | 5 | 7 | 4 | 5 |
| case_010 | 7 | 6 | 8 | 2 | 6 |

### hybrid
| Case | C | D | S | Ci | O |
|---|---|---|---|---|---|
| case_001 | 6 | 5 | 7 | 2 | 5 |
| case_004 | 8 | 7 | 9 | 3 | 7 |
| case_010 | 8 | 7 | 9 | 2 | 7 |

## 关键发现

1. **报告质量**: hybrid 在 LLM-Judge 所有维度（除 citations）上得分更高，overall +0.5
2. **来源丰富度**: hybrid 平均 52 个来源 vs baseline 38 个（+37%）
3. **查询覆盖**: hybrid 0.60 vs baseline 0.50，说明 tree 分支搜索更多元
4. **稳定性风险**: hybrid 完成率 3/5 vs baseline 4/5，2 个 case 超时(900s)
5. **耗时**: hybrid 完成 case 平均 300s vs baseline 174s（+72%），主要因 coordinator 多轮迭代
6. **引用质量**: citations 维度两者都偏低（2-3/10），是共同短板
7. **事实验证**: hybrid claims_verified=0，ClaimVerifier token 匹配法的局限性

## 改进方向

- **降低超时风险**: 限制 coordinator 最大迭代次数或加 wall-clock budget
- **引用改进**: writer prompt 需强化引用格式要求
- **事实验证**: 当前 ClaimVerifier 基于 token 重叠，考虑使用语义匹配
