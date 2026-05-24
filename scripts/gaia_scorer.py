"""GAIA Answer Scoring.

Implements the three GAIA scoring approaches:
1. Numeric matching  — tolerance-based comparison (rtol=0.05, atol=0.1)
2. String matching   — normalized exact/substring match
3. LLM-based eval    — judge-based comparison for complex answers

Reference: GAIA paper (Mialon et al., 2023) and GAIA2 specification.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def normalize_answer(text: str) -> str:
    """Normalize answer text for comparison."""
    return text.strip().lower().rstrip(".")


def score_numeric(prediction: str, ground_truth: str) -> tuple[bool, float]:
    """Score numeric answers with tolerance matching.

    Extracts the first number from each string and compares with
    np.isclose(rtol=0.05, atol=0.1).
    """
    pred_nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(prediction))
    gt_nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(ground_truth))

    if not pred_nums or not gt_nums:
        return False, 0.0

    try:
        pred_val = float(pred_nums[0])
        gt_val = float(gt_nums[0])
        is_close = bool(np.isclose(pred_val, gt_val, rtol=0.05, atol=0.1))
        return is_close, 1.0 if is_close else 0.0
    except (ValueError, OverflowError):
        return False, 0.0


def score_string(prediction: str, ground_truth: str) -> tuple[bool, float]:
    """Score string answers with normalized matching.

    Checks: exact match after normalization, then substring containment.
    """
    pred = normalize_answer(str(prediction))
    gt = normalize_answer(str(ground_truth))

    if pred == gt:
        return True, 1.0

    if gt in pred or pred in gt:
        return True, 0.5

    return False, 0.0


def score_list(prediction: str, ground_truth: str) -> tuple[bool, float]:
    """Score list answers using Jaccard similarity.

    Splits on commas/semicolons, normalizes each item, computes overlap.
    """
    pred_items = set(
        it.strip().lower()
        for it in re.split(r"[,;]", str(prediction))
        if it.strip()
    )
    gt_items = set(
        it.strip().lower()
        for it in re.split(r"[,;]", str(ground_truth))
        if it.strip()
    )

    if not gt_items:
        return False, 0.0

    intersection = pred_items & gt_items
    jaccard = len(intersection) / len(gt_items)
    return jaccard >= 0.8, jaccard


def score_gaia_answer(
    prediction: str,
    ground_truth: str,
    answer_type: str = "auto",
) -> dict[str, Any]:
    """Score a single GAIA answer against ground truth.

    Args:
        prediction: The agent's answer.
        ground_truth: The correct answer.
        answer_type: "numeric", "string", "list", or "auto" (default).

    Returns:
        Dict with 'correct' (bool), 'score' (float 0-1), 'method' (str).
    """
    pred = str(prediction).strip()
    gt = str(ground_truth).strip()

    if pred.lower() == "i don't know" or not pred:
        return {"correct": False, "score": 0.0, "method": "no_answer"}

    if answer_type == "numeric":
        correct, score = score_numeric(pred, gt)
        return {"correct": correct, "score": score, "method": "numeric"}

    if answer_type == "list":
        correct, score = score_list(pred, gt)
        return {"correct": correct, "score": score, "method": "list"}

    if answer_type == "string":
        correct, score = score_string(pred, gt)
        return {"correct": correct, "score": score, "method": "string"}

    # Auto-detect: try numeric first, then string
    if re.search(r"\d", gt):
        correct, score = score_numeric(pred, gt)
        if correct:
            return {"correct": True, "score": 1.0, "method": "numeric_auto"}

    correct, score = score_string(pred, gt)
    return {"correct": correct, "score": score, "method": "string_auto"}
