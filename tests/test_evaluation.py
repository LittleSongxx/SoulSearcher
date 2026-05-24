"""Unit tests for evaluation components — GAIA scorer and rubric system."""

from __future__ import annotations

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
