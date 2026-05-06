from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from eval.deep_research_benchmark.schemas import BenchmarkTask


class DatasetValidationError(ValueError):
    pass


def _clean_string_list(value: Any, *, field_name: str, line_number: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DatasetValidationError(f"Line {line_number}: {field_name} must be a list")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    return cleaned


def _clean_dict(value: Any, *, field_name: str, line_number: int) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DatasetValidationError(f"Line {line_number}: {field_name} must be an object")
    return value


def parse_task(raw: dict[str, Any], *, line_number: int) -> BenchmarkTask:
    if not isinstance(raw, dict):
        raise DatasetValidationError(f"Line {line_number}: expected JSON object")

    query = str(raw.get("query") or "").strip()
    if not query:
        raise DatasetValidationError(f"Line {line_number}: query is required")

    task_id = str(raw.get("id") or f"case_{line_number:03d}").strip()
    if not task_id:
        raise DatasetValidationError(f"Line {line_number}: id must not be empty")

    expected_dimensions = _clean_string_list(
        raw.get("expected_dimensions", raw.get("expected_fields")),
        field_name="expected_dimensions",
        line_number=line_number,
    )
    if not expected_dimensions:
        raise DatasetValidationError(
            f"Line {line_number}: expected_dimensions must contain at least one item"
        )

    return BenchmarkTask(
        id=task_id,
        query=query,
        domain=str(raw.get("domain") or raw.get("metadata", {}).get("domain") or "general"),
        task_type=str(raw.get("task_type") or "open_research"),
        difficulty=str(raw.get("difficulty") or "medium"),
        expected_dimensions=expected_dimensions,
        freshness_requirement=str(raw.get("freshness_requirement") or ""),
        must_cite=bool(raw.get("must_cite", True)),
        judge_rubric=_clean_dict(
            raw.get("judge_rubric"), field_name="judge_rubric", line_number=line_number
        ),
        source_constraints=_clean_dict(
            raw.get("source_constraints"), field_name="source_constraints", line_number=line_number
        ),
        metadata=_clean_dict(raw.get("metadata"), field_name="metadata", line_number=line_number),
    )


def load_tasks(path: str | Path, max_cases: Optional[int] = None) -> list[BenchmarkTask]:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"Benchmark dataset not found: {input_path}")

    tasks: list[BenchmarkTask] = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            raw = json.loads(text)
            tasks.append(parse_task(raw, line_number=line_number))
            if max_cases is not None and len(tasks) >= max_cases:
                break
    return tasks


def write_tasks(path: str | Path, tasks: Iterable[BenchmarkTask]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")
