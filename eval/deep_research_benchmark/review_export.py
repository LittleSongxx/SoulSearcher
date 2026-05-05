from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def export_review_sample(
    run_dir: str | Path,
    *,
    sample_rate: float = 0.2,
    output_name: str = "review_sample.csv",
) -> Path:
    root = Path(run_dir)
    scores_path = root / "judge_scores.json"
    results_path = root / "results.json"
    scores = json.loads(scores_path.read_text(encoding="utf-8")) if scores_path.exists() else []
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    by_case = {str(item.get("case_id")): item for item in results if isinstance(item, dict)}

    rows: list[dict[str, Any]] = []
    for score in scores if isinstance(scores, list) else []:
        if not isinstance(score, dict):
            continue
        if len(rows) > 0 and sample_rate < 1.0:
            interval = max(1, round(1 / max(0.01, sample_rate)))
            if len(rows) % interval != 0:
                continue
        case = by_case.get(str(score.get("case_id"))) or {}
        rows.append(
            {
                "case_id": score.get("case_id", ""),
                "judge_type": score.get("judge_type", ""),
                "status": score.get("status", ""),
                "score": score.get("score", ""),
                "passed": score.get("passed", ""),
                "query": case.get("query", ""),
                "report_excerpt": str(case.get("final_report") or "")[:1200],
                "judge_details": json.dumps(score.get("details") or {}, ensure_ascii=False),
                "human_verdict": "",
                "human_notes": "",
            }
        )

    output = root / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "judge_type",
                "status",
                "score",
                "passed",
                "query",
                "report_excerpt",
                "judge_details",
                "human_verdict",
                "human_notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return output
