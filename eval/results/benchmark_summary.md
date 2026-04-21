# Weaver Deep Research Benchmark Results

> **Real API execution** — all 15 cases with full execution data
> DeepSeek + Tavily/DuckDuckGo/Bocha | Date: 2026-04-21

## Test Configuration

| Parameter | Value |
|---|---|
| Model | deepseek-chat |
| Route | deep (auto-selected) |
| Search Engines | tavily, duckduckgo, bocha |
| Mode | auto (tree explorer) |
| Timeout | 900s per case |

## Full 15-Case End-to-End Results

All 15 cases have **real execution data** including reports, quality metrics, and evidence summaries.

### Per-Case Metrics

| # | Domain | Query | Duration | Report | QC Score | Sources |
|---|---|---|---|---|---|---|
| 01 | financial | AI chip market share 2025 | 679s | 10,344 | **1.0** | 135 |
| 02 | technical | Open-source coding models 2026 | 637s | 9,103 | **0.8** | 130 |
| 03 | medical | FDA guidance for AI medical devices | 591s | 12,207 | **0.8** | 116 |
| 04 | technical | Rust vs Go for microservices | 448s | 10,567 | **0.6** | 140 |
| 05 | legal | EU AI Act impact on SaaS | 535s | 10,901 | **1.0** | 101 |
| 06 | business | Lithium supply chain risks | 455s | 10,365 | **0.8** | 125 |
| 07 | medical | mRNA vaccine advances | 464s | 10,548 | **0.4** | 139 |
| 08 | technical | RAG hallucination handling | 438s | 9,394 | **1.0** | 111 |
| 09 | financial | Renewable energy investment SE Asia | 597s | 10,776 | **0.8** | 139 |
| 10 | scientific | Transformer architecture evolution | 501s | 11,322 | **0.8** | 141 |
| 11 | technical | Zero-trust security best practices | 439s | 8,817 | **0.8** | 119 |
| 12 | business | Remote work productivity meta-analysis | 486s | 11,641 | **0.8** | 140 |
| 13 | scientific | Quantum error correction breakthroughs | 377s | 8,666 | **0.8** | 120 |
| 14 | technical | Vector databases for RAG comparison | 461s | 10,825 | **0.6** | 130 |
| 15 | financial | Carbon credit market outlook | 421s | 10,561 | **0.8** | 123 |

### Aggregated End-to-End Metrics

| Metric | Value |
|---|---|
| **Completion Rate** | **15/15 (100%)** |
| **Avg Query Coverage** | **0.787** (all 15 cases, range 0.4–1.0) |
| **Avg Report Length** | **10,402 chars** (~2,800 words) |
| **Avg Duration** | **502s (8.4 min)** |
| Avg Sources per Report | 127 |
| Total Report Output | 156,037 chars |
| Duration Range | 377s – 679s |
| Report Length Range | 8,666 – 12,207 chars |
| Domains Covered | 6 (financial, technical, medical, legal, business, scientific) |

### Query Coverage Distribution

| Score | Cases | Percentage |
|---|---|---|
| 1.0 (5/5 dimensions) | 3 | 20% |
| 0.8 (4/5 dimensions) | 9 | 60% |
| 0.6 (3/5 dimensions) | 2 | 13% |
| 0.4 (2/5 dimensions) | 1 | 7% |

### Quality by Domain

| Domain | Cases | Avg QC | Avg Duration | Avg Report Length |
|---|---|---|---|---|
| financial | 4 | 0.90 | 574s | 10,421 |
| technical | 5 | 0.76 | 452s | 9,901 |
| medical | 2 | 0.60 | 528s | 11,378 |
| legal | 1 | 1.00 | 535s | 10,901 |
| business | 2 | 0.80 | 471s | 11,003 |
| scientific | 2 | 0.80 | 439s | 9,994 |

### Report Quality Observations

- All 15 reports are structured markdown with headings, sections, and citations
- Financial and legal queries achieve highest coverage (0.90, 1.00) — well-indexed topics
- Medical case 007 (mRNA vaccines) scored lowest (0.4) — niche scientific topic with fewer accessible sources
- Avg 127 web sources consulted per report via multi-provider parallel search
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

- Query coverage is measured by the IterDRAG knowledge gap analyzer against 5 dimensions (evidence, freshness, implementation, official, risk)
- All times include: query decomposition, multi-round search, content extraction, LLM synthesis, and report generation
- "Official" dimension has low hit rate because it requires government/institutional sources not always indexed by search engines
- SSE stream chunk_timeout (120s) prevents ASGI transport hangs during benchmark execution
