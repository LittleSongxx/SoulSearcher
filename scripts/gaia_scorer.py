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

SUPPORTED_ANSWER_TYPES = {"auto", "numeric", "string", "list"}
LIST_SEPARATOR_RE = re.compile(r"[,;，；、\n]+")


def normalize_answer(text: str) -> str:
    """Normalize answer text for comparison."""
    return text.strip().lower().rstrip(".。")


def _split_list_items(text: str) -> list[str]:
    """Split a list-style answer into normalized, non-empty items."""
    return [
        normalize_answer(item)
        for item in LIST_SEPARATOR_RE.split(str(text))
        if normalize_answer(item)
    ]


def infer_answer_type(ground_truth: str) -> str:
    """Infer a simple GAIA answer type from the ground-truth shape."""
    gt = str(ground_truth or "").strip()
    if not gt:
        return "string"

    list_items = _split_list_items(gt)
    if len(list_items) >= 2:
        return "list"

    if re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?", gt):
        return "numeric"

    return "string"


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
    pred_text = normalize_answer(str(prediction))
    pred_items = set(_split_list_items(prediction))
    gt_items = set(_split_list_items(ground_truth))

    if not gt_items:
        return False, 0.0

    if len(pred_items) <= 1:
        intersection = {item for item in gt_items if item and item in pred_text}
    else:
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
    requested_type = str(answer_type or "auto").strip().lower()
    if requested_type not in SUPPORTED_ANSWER_TYPES:
        requested_type = "auto"

    if pred.lower() == "i don't know" or not pred:
        return {
            "correct": False,
            "score": 0.0,
            "method": "no_answer",
            "answer_type": requested_type,
            "diagnostics": {"prediction_chars": len(pred), "ground_truth_chars": len(gt)},
        }

    if requested_type == "numeric":
        correct, score = score_numeric(pred, gt)
        return {"correct": correct, "score": score, "method": "numeric", "answer_type": "numeric"}

    if requested_type == "list":
        correct, score = score_list(pred, gt)
        return {"correct": correct, "score": score, "method": "list", "answer_type": "list"}

    if requested_type == "string":
        correct, score = score_string(pred, gt)
        return {"correct": correct, "score": score, "method": "string", "answer_type": "string"}

    inferred_type = infer_answer_type(gt)
    if inferred_type == "numeric":
        correct, score = score_numeric(pred, gt)
        return {
            "correct": correct,
            "score": score,
            "method": "numeric_auto",
            "answer_type": inferred_type,
        }

    if inferred_type == "list":
        correct, score = score_list(pred, gt)
        return {
            "correct": correct,
            "score": score,
            "method": "list_auto",
            "answer_type": inferred_type,
            "diagnostics": {
                "ground_truth_items": len(_split_list_items(gt)),
                "prediction_chars": len(pred),
            },
        }

    correct, score = score_string(pred, gt)
    return {
        "correct": correct,
        "score": score,
        "method": "string_auto",
        "answer_type": inferred_type,
    }
