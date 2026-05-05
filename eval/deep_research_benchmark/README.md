# Weaver Deep Research Benchmark

This is an independent benchmark system for measuring real Weaver Deep Research performance.
It does not reuse the old smoke benchmark or ablation results.

## What it measures

- Rubric pass rate
- Citation accuracy
- Unsupported claim rate
- Average effective citations
- P50 / P90 end-to-end latency
- Stable completion rate

## Run real benchmark

```bash
conda run -n langchain python -m eval.deep_research_benchmark.cli run \
  --dataset eval/deep_research_benchmark/datasets/seed_tasks.jsonl \
  --output eval/deep_research_benchmark/results/run_manual \
  --base-url asgi \
  --strategy supervisor_workers \
  --max-cases 10 \
  --timeout-s 900
```

The default run command is supervisor-only and uses a large Deep Research budget:

- `deepsearch_supervisor_rounds=3`
- `deepsearch_supervisor_max_workers=4`
- `deepsearch_supervisor_queries_per_worker=3`
- `deepsearch_supervisor_parallel_workers=2`
- `deepsearch_results_per_query=8`
- `deepsearch_max_seconds=840`
- `deepsearch_report_sources_limit=30`

## Judge reports

```bash
conda run -n langchain python -m eval.deep_research_benchmark.cli judge \
  --run-dir eval/deep_research_benchmark/results/run_manual
```

## Summarize metrics

```bash
conda run -n langchain python -m eval.deep_research_benchmark.cli summarize \
  --run-dir eval/deep_research_benchmark/results/run_manual
```

## Export human review sample

```bash
conda run -n langchain python -m eval.deep_research_benchmark.cli export-review \
  --run-dir eval/deep_research_benchmark/results/run_manual \
  --sample-rate 0.2
```

## Notes

- `/api/research/sse` is the real execution entry point.
- `/api/runs/{thread_id}` provides run metrics.
- `/api/sessions/{thread_id}/evidence` provides sources, claims, citation annotations, timeline, and passages.
- Weaver internal quality fields are collected but not used as final judge scores.
