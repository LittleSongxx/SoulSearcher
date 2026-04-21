# Weaver Deep Research Benchmark Results

> **Real API execution** with DeepSeek + Tavily/DuckDuckGo/Bocha
> Date: 2026-04-21 | Full data: `bench_full_15cases.json`

## Test Configuration

| Parameter | Value |
|---|---|
| Model | deepseek-chat |
| Route | deep (auto-selected) |
| Search Engines | tavily, duckduckgo, bocha |
| Mode | auto (tree explorer) |
| Timeout | 900s per case |
| Total wall-clock time | ~127 min |

## Full 15-Case End-to-End Results

### Per-Case Metrics

| # | Domain | Query | Duration | Report Length | Query Coverage | Sources |
|---|---|---|---|---|---|---|
| 01 | financial | AI chip market share 2025 | 620s | 11,238 | **1.0** (5/5) | 135 |
| 02 | technical | Open-source coding models 2026 | 567s | 9,895 | **0.8** (4/5) | 130 |
| 03 | medical | FDA guidance for AI medical devices | 542s | 10,718 | **0.8** (4/5) | 116 |
| 04 | technical | Rust vs Go for microservices | 566s | 11,032 | — | — |
| 05 | legal | EU AI Act impact on SaaS | 538s | 11,511 | — | — |
| 06 | business | Lithium supply chain risks | 556s | 11,089 | — | — |
| 07 | medical | mRNA vaccine advances | 557s | 11,318 | — | — |
| 08 | technical | RAG hallucination handling | 499s | 10,318 | — | — |
| 09 | financial | Renewable energy investment SE Asia | 535s | 12,043 | — | — |
| 10 | scientific | Transformer architecture evolution | 503s | 12,012 | — | — |
| 11 | technical | Zero-trust security best practices | 393s | 10,559 | — | — |
| 12 | business | Remote work productivity meta-analysis | 486s | 11,641 | **0.8** (4/5) | 140 |
| 13 | scientific | Quantum error correction breakthroughs | 377s | 8,666 | **0.8** (4/5) | 120 |
| 14 | technical | Vector databases for RAG comparison | 461s | 10,825 | **0.6** (3/5) | 130 |
| 15 | financial | Carbon credit market outlook | 421s | 10,561 | **0.8** (4/5) | 123 |

*Note: "—" indicates cases where full quality metrics were not captured (run in batch mode with log-only output). Duration and report length are from stdout progress logs.*

### Aggregated End-to-End Metrics

| Metric | Value |
|---|---|
| **Completion Rate** | **15/15 (100%)** |
| **Avg Query Coverage** | **0.80** (7 cases sampled, range 0.6–1.0) |
| **Avg Report Length** | **10,895 chars** (~3,000 words) |
| **Avg Duration** | **508s (8.5 min)** |
| Avg Sources per Report | 128 |
| Avg Unsupported Claims | 9.3 per report |
| Total Report Output | 163,426 chars |
| Duration Range | 377s – 620s |
| Report Length Range | 8,666 – 12,043 chars |
| Domains Covered | 6 (financial, technical, medical, legal, business, scientific) |

### Dimension Coverage Analysis (7 cases with full data)

| Dimension | Hit Rate | Avg Hits |
|---|---|---|
| evidence | 7/7 (100%) | 5.4 |
| freshness | 7/7 (100%) | 4.3 |
| implementation | 7/7 (100%) | 7.9 |
| risk | 7/7 (100%) | 1.7 |
| official | 2/7 (29%) | 1.0 |

### Report Quality Observations

- All 15 reports are structured markdown with headings, sections, and citations
- Reports cover: overview, detailed analysis, trends, risks, and conclusions
- Case 001 achieved full dimension coverage (1.0) — "official" dimension requires government/institutional sources
- Avg 128 web sources consulted per report via multi-provider parallel search
- Tree explorer generates ~32 queries per case across multiple research branches

## Offline Component Benchmarks (no API keys)

Run: `python -m eval.benchmarks.component_bench`

| Component | Metric | Value |
|---|---|---|
| **ClaimVerifier** | Binary Precision | 100% |
| | Binary Recall | 87.5% |
| | F1 Score | 93.3% |
| | 3-Class Accuracy | 87.5% |
| **URL Dedup** | Dedup Ratio | 50% |
| | Merge Accuracy | 100% |
| **Multi-Search Aggregation** | Cross-Provider Dedup | 33.3% |
| | Provider Diversity | 100% |
| **Context Truncation** | Token Reduction | 73.2% |
| | System Message Preserved | Yes |
| | Recent Messages Preserved | Yes |
| **Search Cache** | Hit Ratio (repeated queries) | 50% |
| **Tool Registry** | Success Rate (100 calls) | 81% |

## Reproducing

```bash
# Offline component benchmarks (instant, no API keys)
conda run -n weaver python -m eval.benchmarks.component_bench

# Full end-to-end benchmark (requires API keys, ~2.5 hours)
conda run -n weaver python scripts/benchmark_deep_research.py \
  --max-cases 15 --execute --timeout-s 900 \
  --output eval/results/bench_full_15cases.json
```

## Notes

- "Unsupported claims" count reflects claims lacking direct evidence URL backing — a known characteristic of tree-based exploration where findings are synthesized across branches
- Query coverage is measured by the IterDRAG knowledge gap analyzer against 5 research dimensions (evidence, freshness, implementation, official, risk)
- All times include: query decomposition, multi-round search, content extraction, LLM synthesis, and report generation
- "Official" dimension has low hit rate because it requires government/institutional sources, which are often behind paywalls or not indexed by search engines
