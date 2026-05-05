from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.deep_research_benchmark.judges.run_judges import judge_run
from eval.deep_research_benchmark.metrics import summarize_run
from eval.deep_research_benchmark.reports import write_summary_markdown
from eval.deep_research_benchmark.review_export import export_review_sample
from eval.deep_research_benchmark.runner import run_benchmark
from eval.deep_research_benchmark.schemas import RunConfig


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("run", help="Run real Weaver Deep Research benchmark")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="asgi")
    parser.add_argument("--strategy", default="supervisor_workers")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--user-id", default="benchmark_user")
    parser.add_argument("--deepsearch-config-json", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weaver Deep Research benchmark system")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(subparsers)

    judge_parser = subparsers.add_parser("judge", help="Run LLM-as-Judge for a run directory")
    judge_parser.add_argument("--run-dir", type=Path, required=True)
    judge_parser.add_argument("--judge-model", default="")

    summary_parser = subparsers.add_parser("summarize", help="Aggregate benchmark metrics")
    summary_parser.add_argument("--run-dir", type=Path, required=True)

    review_parser = subparsers.add_parser("export-review", help="Export judge samples for human review")
    review_parser.add_argument("--run-dir", type=Path, required=True)
    review_parser.add_argument("--sample-rate", type=float, default=0.2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        deepsearch_config = None
        if args.deepsearch_config_json:
            loaded = json.loads(args.deepsearch_config_json)
            if not isinstance(loaded, dict):
                raise ValueError("--deepsearch-config-json must decode to a JSON object")
            deepsearch_config = loaded
        config = RunConfig(
            dataset_path=args.dataset,
            output_dir=args.output,
            base_url=args.base_url,
            strategy=args.strategy,
            model=args.model,
            max_cases=args.max_cases,
            timeout_s=args.timeout_s,
            concurrency=max(1, int(args.concurrency)),
            user_id=args.user_id,
        )
        if deepsearch_config is not None:
            config.deepsearch_config.update(deepsearch_config)
        results = run_benchmark(config)
        print(f"Run complete: {len(results)} cases -> {args.output}")
        return 0

    if args.command == "judge":
        scores = judge_run(args.run_dir, judge_model=args.judge_model)
        print(f"Judge complete: {len(scores)} scores -> {args.run_dir / 'judge_scores.json'}")
        return 0

    if args.command == "summarize":
        summary = summarize_run(args.run_dir)
        output = write_summary_markdown(args.run_dir, summary)
        print(f"Summary written: {args.run_dir / 'summary.json'} and {output}")
        return 0

    if args.command == "export-review":
        output = export_review_sample(args.run_dir, sample_rate=args.sample_rate)
        print(f"Review sample written: {output}")
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
