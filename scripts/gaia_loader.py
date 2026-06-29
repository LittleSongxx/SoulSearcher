"""GAIA Dataset Loader.

Loads GAIA questions from HuggingFace datasets or a local JSONL file.
Falls back gracefully when HuggingFace is unavailable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# GAIA dataset on HuggingFace
GAIA_DATASET_PATH = "gaia-benchmark/GAIA"
GAIA_CONFIG = "2023_all"  # Full dataset with all difficulty levels


def load_gaia_from_huggingface(
    split: str = "validation",
    max_questions: int | None = None,
    levels: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Load GAIA questions from HuggingFace datasets.

    Args:
        split: Dataset split — 'validation' (165 questions, public) or 'test'.
        max_questions: Cap the number of questions returned.
        levels: Filter by difficulty level (1, 2, or 3). None = all levels.

    Returns:
        List of question dicts with keys: task_id, Question, Level,
        Final answer, file_name, file_path (if any).
    """
    try:
        from datasets import load_dataset
    except ImportError:
        logger.warning("datasets library not installed. Install with: pip install datasets")
        return []

    try:
        dataset = load_dataset(GAIA_DATASET_PATH, GAIA_CONFIG, split=split)
    except Exception as e:
        logger.warning(f"Failed to load GAIA from HuggingFace: {e}")
        return []

    questions = []
    for row in dataset:
        level = int(row.get("Level", 1))
        if levels and level not in levels:
            continue

        questions.append({
            "task_id": str(row.get("task_id", "")),
            "question": str(row.get("Question", "")),
            "level": level,
            "ground_truth": str(row.get("Final answer", "")),
            "file_name": str(row.get("file_name", "")),
            "file_path": str(row.get("file_path", "")),
        })

        if max_questions and len(questions) >= max_questions:
            break

    logger.info(f"Loaded {len(questions)} GAIA questions from HF (split={split})")
    return questions


def load_gaia_from_local(path: str | Path) -> list[dict[str, Any]]:
    """Load GAIA questions from a local JSONL or JSON file.

    Expected format (JSONL, one JSON object per line):
      {"task_id": "...", "Question": "...", "Level": 1, "Final answer": "..."}

    Or JSON array of such objects.
    """
    path = Path(path)
    if not path.exists():
        logger.error(f"GAIA local file not found: {path}")
        return []

    with open(path, encoding="utf-8") as f:
        content = f.read().strip()

    questions = []
    if content.startswith("["):
        data = json.loads(content)
        for row in data:
            questions.append(_parse_gaia_row(row))
    else:
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                questions.append(_parse_gaia_row(json.loads(line)))
            except json.JSONDecodeError:
                continue

    logger.info(f"Loaded {len(questions)} GAIA questions from local: {path}")
    return questions


def _parse_gaia_row(row: dict) -> dict[str, Any]:
    return {
        "task_id": str(row.get("task_id", "")),
        "question": str(row.get("Question", "")),
        "level": int(row.get("Level", 1)),
        "ground_truth": str(row.get("Final answer", "")),
        "file_name": str(row.get("file_name", "")),
        "file_path": str(row.get("file_path", "")),
    }


# =============================================================================
# Built-in curated benchmark queries (SoulSearcher-specific, not GAIA)
# =============================================================================

CURATED_BENCHMARK = [
    # Level 1 — Simple facts (~3-5 steps)
    {
        "id": "wv_simple_01",
        "query": "2024年诺贝尔物理学奖获得者是谁？",
        "level": 1,
        "expected_length": (100, 500),
        "ground_truth": "John Hopfield, Geoffrey Hinton",
    },
    {
        "id": "wv_simple_02",
        "query": "Python 3.12 中 f-string 相比 3.11 有什么新特性？",
        "level": 1,
        "expected_length": (200, 800),
        "ground_truth": "f-string, 表达式, 引号, 嵌套, 多行, 反斜杠",
    },
    {
        "id": "wv_simple_03",
        "query": "OpenAI 最新发布的 GPT 模型是什么版本，发布时间是哪天？",
        "level": 1,
        "expected_length": (100, 400),
        "ground_truth": "GPT, OpenAI, 发布",
    },
    # Level 2 — Comparisons and analysis (~5-10 steps)
    {
        "id": "wv_compare_01",
        "query": "对比 Qwen 3 和 DeepSeek 系列模型在推理能力、训练成本和开源策略上的差异",
        "level": 2,
        "expected_length": (500, 2000),
        "ground_truth": "Qwen, DeepSeek, 推理, 训练, 开源, 成本, MoE",
    },
    {
        "id": "wv_compare_02",
        "query": "分析 2024-2025 年全球电动汽车市场格局变化，重点关注比亚迪和特斯拉的竞争态势",
        "level": 2,
        "expected_length": (500, 2000),
        "ground_truth": "比亚迪, 特斯拉, 电动汽车, 市场, 电池, 销量",
    },
    {
        "id": "wv_compare_03",
        "query": "Rust 和 Zig 在系统编程中的定位有什么不同？各自适合什么场景？",
        "level": 2,
        "expected_length": (400, 1500),
        "ground_truth": "Rust, Zig, 内存安全, 所有权, 编译时, 分配器, 系统编程",
    },
    # Level 3 — Deep analysis (>10 steps)
    {
        "id": "wv_deep_01",
        "query": "深入分析 Transformer 架构从 2017 年到 2025 年的演进历程，包括关键变体（BERT、GPT、T5、Vision Transformer、Sora）的设计创新和影响，以及对未来架构发展的展望",
        "level": 3,
        "expected_length": (1000, 4000),
        "ground_truth": "Transformer, 自注意力, BERT, GPT, T5, Vision Transformer, Sora, MoE, 位置编码, 归一化",
    },
    {
        "id": "wv_deep_02",
        "query": "全面梳理 AI Agent 技术栈的发展现状：从 ReAct、Plan-and-Solve 到多智能体协作框架，对比主流框架（LangGraph、CrewAI、AutoGen、OpenAI Swarm）的设计理念和适用场景",
        "level": 3,
        "expected_length": (1000, 4000),
        "ground_truth": "AI Agent, ReAct, LangGraph, CrewAI, AutoGen, 多智能体, 工具调用, 规划, 协作",
    },
    {
        "id": "wv_deep_03",
        "query": "分析全球半导体产业链从 2020 年到 2025 年的重构过程，包括中美技术竞争、芯片法案影响、先进制程竞赛和供应链多元化趋势",
        "level": 3,
        "expected_length": (1000, 4000),
        "ground_truth": "半导体, 芯片, 台积电, 三星, 英特尔, 先进制程, 供应链, CHIPS法案, 光刻, 封装",
    },
]


def get_curated_benchmark(level: int | None = None) -> list[dict[str, Any]]:
    """Return the built-in curated benchmark queries."""
    if level:
        return [q for q in CURATED_BENCHMARK if q["level"] == level]
    return list(CURATED_BENCHMARK)
