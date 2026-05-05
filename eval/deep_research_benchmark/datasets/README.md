# Deep Research Benchmark Datasets

`seed_tasks.jsonl` is a small starter dataset for validating the new benchmark pipeline.
Expand it to 50+ cases before using the aggregate metrics in a resume or report.

Each line is a JSON object with:

- `id`
- `query`
- `domain`
- `task_type`
- `difficulty`
- `expected_dimensions`
- `freshness_requirement`
- `must_cite`
- `judge_rubric`
- `source_constraints`
- `metadata`
