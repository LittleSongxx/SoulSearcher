"""Unit tests for evaluation components — GAIA scorer and rubric system."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestGAIAScorer:
    """Tests for scripts/gaia_scorer.py — pure scoring functions."""

    def test_numeric_exact_match(self):
        from scripts.gaia_scorer import score_numeric
        correct, score = score_numeric("42", "42")
        assert correct
        assert score == 1.0

    def test_numeric_within_tolerance(self):
        from scripts.gaia_scorer import score_numeric
        correct, score = score_numeric("100", "103")
        assert correct  # within 5% rtol

    def test_numeric_no_match(self):
        from scripts.gaia_scorer import score_numeric
        correct, score = score_numeric("100", "200")
        assert not correct

    def test_numeric_no_numbers(self):
        from scripts.gaia_scorer import score_numeric
        correct, score = score_numeric("hello", "world")
        assert not correct

    def test_string_exact_match(self):
        from scripts.gaia_scorer import score_string
        correct, score = score_string("Paris", "Paris")
        assert correct
        assert score == 1.0

    def test_string_normalized_match(self):
        from scripts.gaia_scorer import score_string
        correct, score = score_string("  Paris.  ", "paris")
        assert correct
        assert score == 1.0

    def test_string_substring_match(self):
        from scripts.gaia_scorer import score_string
        correct, score = score_string("Paris, France", "Paris")
        assert correct
        assert score == 0.5

    def test_string_no_match(self):
        from scripts.gaia_scorer import score_string
        correct, score = score_string("Paris", "London")
        assert not correct

    def test_list_jaccard_match(self):
        from scripts.gaia_scorer import score_list
        correct, score = score_list("a, b, c, d", "a, b, c")
        assert correct  # 3/3 = 1.0 >= 0.8

    def test_list_jaccard_no_match(self):
        from scripts.gaia_scorer import score_list
        correct, score = score_list("x, y", "a, b, c")
        assert not correct

    def test_gaia_answer_auto_numeric(self):
        from scripts.gaia_scorer import score_gaia_answer
        result = score_gaia_answer("42", "42")
        assert result["correct"]
        assert "numeric" in result["method"]

    def test_gaia_answer_auto_string(self):
        from scripts.gaia_scorer import score_gaia_answer
        result = score_gaia_answer("Paris", "Paris")
        assert result["correct"]

    def test_gaia_answer_auto_list_uses_keyword_coverage(self):
        from scripts.gaia_scorer import score_gaia_answer

        result = score_gaia_answer(
            "The answer names John Hopfield and Geoffrey Hinton.",
            "John Hopfield, Geoffrey Hinton",
        )

        assert result["correct"]
        assert result["method"] == "list_auto"
        assert result["answer_type"] == "list"

    def test_infer_answer_type(self):
        from scripts.gaia_scorer import infer_answer_type

        assert infer_answer_type("42") == "numeric"
        assert infer_answer_type("Paris") == "string"
        assert infer_answer_type("A; B; C") == "list"

    def test_gaia_answer_no_answer(self):
        from scripts.gaia_scorer import score_gaia_answer
        result = score_gaia_answer("I don't know", "Paris")
        assert not result["correct"]
        assert result["method"] == "no_answer"

    def test_normalize_answer(self):
        from scripts.gaia_scorer import normalize_answer
        assert normalize_answer("  Hello World.  ") == "hello world"


class TestRubric:
    """Basic structural tests for rubric models."""

    def test_rubric_models_import(self):
        from agent.workflows.rubric import (
            RubricItem,
            RubricDimension,
            RubricResult,
        )
        item = RubricItem(id="test_1", criterion="Test criterion", weight=1.0, score=0.5, evidence="ok")
        assert item.score == 0.5
        assert item.weight == 1.0

    def test_rubric_result_aggregate(self):
        from agent.workflows.rubric import RubricItem, RubricDimension, RubricResult
        dim = RubricDimension(
            name="test_dim",
            weight=1.0,
            items=[RubricItem(id="i1", criterion="c1", weight=1.0, score=1.0, evidence="good")],
        )
        result = RubricResult(dimensions=[dim], overall_score=0.0, verdict="pending")
        assert len(result.dimensions) == 1

    def test_rubric_dimension_weight_preserved(self):
        """Regression: weight field must be stored, not silently dropped by Pydantic."""
        from agent.workflows.rubric import RubricDimension
        dim = RubricDimension(name="w", weight=2.5, items=[])
        assert dim.weight == 2.5

    def test_rubric_result_compute_overall(self):
        """Weighted aggregation across dimensions must work."""
        from agent.workflows.rubric import RubricItem, RubricDimension, RubricResult
        d1 = RubricDimension(
            name="d1", weight=2.0,
            items=[RubricItem(id="a", criterion="c", weight=1.0, score=1.0, evidence="")],
        )
        d2 = RubricDimension(
            name="d2", weight=1.0,
            items=[RubricItem(id="b", criterion="c", weight=1.0, score=0.0, evidence="")],
        )
        d1.score = d1.compute_score()
        d2.score = d2.compute_score()
        result = RubricResult(dimensions=[d1, d2], overall_score=0.0, verdict="pending")
        score = result.compute_overall()
        # (1.0 * 2.0 + 0.0 * 1.0) / (2.0 + 1.0) = 2.0/3.0 ≈ 0.667
        assert abs(score - 0.667) < 0.001

    def test_rubric_parse_invalid_json_is_incomplete(self):
        from agent.workflows.rubric import L1_RUBRIC, _parse_rubric_response

        result = _parse_rubric_response("not-json", L1_RUBRIC, "l1")
        assert not result.passed
        assert result.verdict == "incomplete"

    def test_rubric_parse_uses_provided_weights(self):
        from agent.workflows.rubric import _parse_rubric_response

        rubric_definition = [
            {
                "name": "critical",
                "description": "",
                "weight": 2.0,
                "items": [{"id": "a", "criterion": "", "weight": 1.0}],
            },
            {
                "name": "secondary",
                "description": "",
                "weight": 1.0,
                "items": [{"id": "b", "criterion": "", "weight": 1.0}],
            },
        ]
        content = json.dumps(
            {
                "dimensions": [
                    {"name": "critical", "items": [{"id": "a", "score": 1.0, "evidence": "ok"}]},
                    {"name": "secondary", "items": [{"id": "b", "score": 0.0, "evidence": "bad"}]},
                ]
            }
        )

        result = _parse_rubric_response(content, rubric_definition, "l2")
        assert abs(result.overall_score - (2.0 / 3.0)) < 0.001

    def test_extract_source_texts_prefers_artifacts(self):
        from agent.workflows.rubric import extract_source_texts

        state = {
            "deepsearch_artifacts": {
                "passages": [
                    {"url": "https://example.com", "text": "artifact evidence text"}
                ]
            },
            "notes": ["fallback note"],
        }

        text = extract_source_texts(state)
        assert "https://example.com" in text
        assert "artifact evidence text" in text

    def test_quality_response_invalid_json_is_incomplete(self):
        from agent.workflows.quality_check import _parse_quality_response

        result = _parse_quality_response("broken")
        assert not result.passed
        assert result.verdict == "incomplete"


class TestBenchmarkPreflight:
    """Pure checks for benchmark input/rubric sanity helpers."""

    def test_deep_research_benchmark_preflight_accepts_curated_cases(self):
        from scripts.benchmark_deep_research import (
            BENCHMARK_CASES,
            validate_benchmark_cases,
            validate_rubric_definitions,
        )

        case_result = validate_benchmark_cases(BENCHMARK_CASES)
        rubric_result = validate_rubric_definitions()

        assert case_result["errors"] == []
        assert rubric_result["errors"] == []
        assert case_result["level_counts"][1] > 0
        assert case_result["level_counts"][2] > 0
        assert case_result["level_counts"][3] > 0

    def test_deep_research_benchmark_preflight_rejects_duplicate_ids(self):
        from scripts.benchmark_deep_research import validate_benchmark_cases

        cases = [
            {
                "id": "dup",
                "query": "A sufficiently long question?",
                "level": 1,
                "min_chars": 100,
                "min_citations": 1,
            },
            {
                "id": "dup",
                "query": "Another sufficiently long question?",
                "level": 1,
                "min_chars": 100,
                "min_citations": 1,
            },
        ]

        result = validate_benchmark_cases(cases)

        assert any("duplicate case id" in error for error in result["errors"])

    def test_gaia_benchmark_preflight_counts_answer_types(self):
        from scripts.gaia_benchmark import validate_gaia_questions

        questions = [
            {
                "task_id": "q1",
                "question": "What number?",
                "level": 1,
                "ground_truth": "42",
            },
            {
                "task_id": "q2",
                "question": "Who are they?",
                "level": 1,
                "ground_truth": "A, B",
            },
        ]

        result = validate_gaia_questions(questions)

        assert result["errors"] == []
        assert result["answer_type_counts"]["numeric"] == 1
        assert result["answer_type_counts"]["list"] == 1

    def test_gaia_benchmark_preflight_requires_scoring_signal(self):
        from scripts.gaia_benchmark import validate_gaia_questions

        result = validate_gaia_questions([
            {"task_id": "q1", "question": "What happened?", "level": 1}
        ])

        assert any("ground_truth or expected_length" in error for error in result["errors"])
