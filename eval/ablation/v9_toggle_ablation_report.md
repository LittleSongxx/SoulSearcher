# v9 优化开关定位实验报告

**日期**: 2026-04-27 01:40  
**分支**: dev2  
**模型**: deepseek-v4-flash  
**样本**: v7_010, v7_008, v7_001, v7_006, v7_004（5 个代表 case）  
**Judge**: 当前 PRIMARY_MODEL, 完整报告, 每 case 3 次评分取平均

## 一、当前 .env 快照

| 参数 | 值 |
|---|---|
| PRIMARY_MODEL | deepseek-v4-flash |
| REASONING_MODEL | deepseek-v4-pro |
| SEARCH_ENGINES | tavily,bocha |
| SEARCH_STRATEGY | fallback |
| TOOL_RETRY | true |
| TOOL_CALL_LIMIT | 12 |
| DEEPSEARCH_MAX_EPOCHS | 3 |
| DEEPSEARCH_QUERY_NUM | 5 |
| DEEPSEARCH_RESULTS_PER_QUERY | 5 |
| DEEPSEARCH_REPORT_SOURCES_LIMIT | 30 |
| DEEPSEARCH_ENABLE_RESEARCH_FETCHER | true |
| RESEARCH_FETCH_TIMEOUT_S | 15 |
| RESEARCH_FETCH_CONCURRENCY | 10 |
| RESEARCH_FETCH_CACHE_TTL_S | 0 |
| RESEARCH_FETCH_RENDER_MODE | off |
| CRAWLER_HEADLESS | false |

---

## 二、总体排名

| Variant | 完成率 | 平均耗时 | 报告长度 | QC | Src | CitCov | J-Citations | J-Overall |
|---|---|---|---|---|---|---|---|---|
| optimized_full | 5/5 | 239s | 12,997 | 0.560 | 127.8 | 0.711 | 4.93 | 6.86 |
| no_obs_masking | 5/5 | 231s | 11,309 | 0.680 | 128.0 | 0.638 | 5.53 | 7.06 |
| no_offloading | 5/5 | 253s | 13,883 | 0.680 | 126.0 | 0.595 | 6.07 | 7.53 |
| no_reflexion | 5/5 | 276s | 12,102 | 0.600 | 128.2 | 0.695 | 5.93 | 7.33 |
| no_backtrack | 5/5 | 240s | 12,360 | 0.680 | 119.0 | 0.662 | 5.33 | 6.87 |
| no_tool_pruning | 5/5 | 253s | 14,113 | 0.760 | 126.0 | 0.552 | 6.47 | 7.80 |
| baseline_equivalent | 5/5 | 233s | 11,466 | 0.680 | 126.6 | 0.716 | 4.67 | 6.87 |
| context_only | 5/5 | 224s | 12,174 | 0.680 | 122.6 | 0.669 | 5.40 | 7.00 |
| search_planning_only | 5/5 | 218s | 13,206 | 0.520 | 125.4 | 0.636 | 6.60 | 7.53 |

## 三、关键观察

- **最佳 Overall**：`no_tool_pruning` = 7.80
- **最佳 Citations**：`search_planning_only` = 6.60
- **optimized_full vs baseline_equivalent**：Overall 6.86 vs 6.87；Citations 4.93 vs 4.67
- `no_offloading` 相对 `optimized_full`：ΔOverall=+0.67，ΔCitations=+1.14
- `no_obs_masking` 相对 `optimized_full`：ΔOverall=+0.20，ΔCitations=+0.60
- `no_reflexion` 相对 `optimized_full`：ΔOverall=+0.47，ΔCitations=+1.00
- `no_backtrack` 相对 `optimized_full`：ΔOverall=+0.01，ΔCitations=+0.40
- `no_tool_pruning` 相对 `optimized_full`：ΔOverall=+0.94，ΔCitations=+1.54

## 四、逐 Case × Variant Judge 对比

| Case | Domain | Variant | Time | Src | CitCov | J-Citations | J-Overall |
|---|---|---|---|---|---|---|---|
| v7_010 | scientific | optimized_full | 242s | 130 | 0.686 | 5.33 | 7.33 |
| v7_008 | medical | optimized_full | 207s | 141 | 0.691 | 6.00 | 7.33 |
| v7_001 | financial | optimized_full | 237s | 124 | 0.667 | 5.67 | 7.33 |
| v7_006 | legal | optimized_full | 274s | 148 | 0.781 | 0.67 | 4.33 |
| v7_004 | technical | optimized_full | 235s | 96 | 0.731 | 7.00 | 8.00 |
| v7_010 | scientific | no_obs_masking | 242s | 135 | 0.615 | 7.00 | 8.00 |
| v7_008 | medical | no_obs_masking | 194s | 128 | 0.324 | 4.33 | 6.33 |
| v7_001 | financial | no_obs_masking | 177s | 115 | 0.784 | 5.33 | 6.33 |
| v7_006 | legal | no_obs_masking | 282s | 152 | 0.556 | 3.67 | 6.33 |
| v7_004 | technical | no_obs_masking | 259s | 110 | 0.913 | 7.33 | 8.33 |
| v7_010 | scientific | no_offloading | 300s | 141 | 0.507 | 6.33 | 7.67 |
| v7_008 | medical | no_offloading | 207s | 134 | 0.783 | 4.67 | 6.33 |
| v7_001 | financial | no_offloading | 209s | 103 | 0.837 | 5.67 | 7.67 |
| v7_006 | legal | no_offloading | 294s | 145 | 0.568 | 6.00 | 7.67 |
| v7_004 | technical | no_offloading | 255s | 107 | 0.280 | 7.67 | 8.33 |
| v7_010 | scientific | no_reflexion | 290s | 131 | 0.700 | 7.33 | 8.00 |
| v7_008 | medical | no_reflexion | 240s | 134 | 0.349 | 3.67 | 6.67 |
| v7_001 | financial | no_reflexion | 237s | 131 | 0.800 | 4.00 | 6.00 |
| v7_006 | legal | no_reflexion | 342s | 134 | 0.857 | 6.67 | 7.67 |
| v7_004 | technical | no_reflexion | 270s | 111 | 0.767 | 8.00 | 8.33 |
| v7_010 | scientific | no_backtrack | 276s | 118 | 0.400 | 3.33 | 5.67 |
| v7_008 | medical | no_backtrack | 196s | 127 | 0.706 | 4.33 | 6.00 |
| v7_001 | financial | no_backtrack | 236s | 139 | 0.614 | 4.33 | 6.33 |
| v7_006 | legal | no_backtrack | 240s | 104 | 0.667 | 7.00 | 8.00 |
| v7_004 | technical | no_backtrack | 254s | 107 | 0.923 | 7.67 | 8.33 |
| v7_010 | scientific | no_tool_pruning | 271s | 136 | 0.303 | 4.67 | 6.67 |
| v7_008 | medical | no_tool_pruning | 195s | 135 | 0.684 | 7.00 | 8.33 |
| v7_001 | financial | no_tool_pruning | 245s | 127 | 0.600 | 7.33 | 8.00 |
| v7_006 | legal | no_tool_pruning | 236s | 124 | 0.610 | 6.67 | 8.00 |
| v7_004 | technical | no_tool_pruning | 319s | 108 | 0.561 | 6.67 | 8.00 |
| v7_010 | scientific | baseline_equivalent | 206s | 133 | 0.710 | 2.67 | 5.67 |
| v7_008 | medical | baseline_equivalent | 189s | 135 | 0.621 | 4.67 | 7.00 |
| v7_001 | financial | baseline_equivalent | 225s | 135 | 0.667 | 6.33 | 7.33 |
| v7_006 | legal | baseline_equivalent | 308s | 132 | 0.704 | 2.33 | 6.00 |
| v7_004 | technical | baseline_equivalent | 236s | 98 | 0.879 | 7.33 | 8.33 |
| v7_010 | scientific | context_only | 210s | 133 | 0.765 | 3.33 | 5.33 |
| v7_008 | medical | context_only | 204s | 135 | 0.692 | 7.00 | 8.67 |
| v7_001 | financial | context_only | 194s | 126 | 0.718 | 6.33 | 6.67 |
| v7_006 | legal | context_only | 253s | 124 | 0.556 | 2.33 | 6.00 |
| v7_004 | technical | context_only | 258s | 95 | 0.615 | 8.00 | 8.33 |
| v7_010 | scientific | search_planning_only | 229s | 131 | 0.595 | 6.00 | 7.33 |
| v7_008 | medical | search_planning_only | 202s | 136 | 0.613 | 5.00 | 6.00 |
| v7_001 | financial | search_planning_only | 202s | 125 | 0.522 | 7.33 | 8.00 |
| v7_006 | legal | search_planning_only | 208s | 113 | 0.826 | 7.00 | 8.00 |
| v7_004 | technical | search_planning_only | 247s | 122 | 0.622 | 7.67 | 8.33 |

## 五、最可能的责任开关判断

- 如果 `no_offloading` 或 `no_obs_masking` 明显提升 `J-Citations`，优先怀疑上下文压缩导致引用映射丢失。
- 如果 `no_reflexion` / `no_backtrack` / `no_tool_pruning` 改善更明显，则更可能是搜索分支扩张后 source selection 质量下降。
- 如果 `context_only` 优于 `search_planning_only`，说明上下文优化保留、搜索规划开关收缩更适合当前 v9 环境。

## 六、输出文件

- 结果 JSON: `/home/song/code/Agent/Weaver/eval/ablation/results/v9_ablation_*.json`
- Judge JSON: `/home/song/code/Agent/Weaver/eval/ablation/results/v9_ablation_judge_scores.json`
- 本报告: `/home/song/code/Agent/Weaver/eval/ablation/v9_toggle_ablation_report.md`
