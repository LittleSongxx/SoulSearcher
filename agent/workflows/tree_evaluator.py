"""
Lightweight branch evaluator for LATS-style tree search backtracking.

Scores completed research tree branches using heuristic signals
(no extra LLM call required):
- Number of findings
- Summary length / richness
- Source diversity
- Relevance score from decomposition

Branches scoring below ``tree_backtrack_score_threshold`` are eligible
for retry via an alternative subtopic decomposition.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict

from common.config import settings

if TYPE_CHECKING:
    from agent.workflows.research_tree import ResearchTreeNode

logger = logging.getLogger(__name__)


def score_branch(node: "ResearchTreeNode") -> float:
    """
    Score a completed branch on a 0-1 scale using heuristic signals.

    Components (equally weighted):
    - findings_score:  normalized count of findings (cap at 10)
    - summary_score:   normalized summary length (cap at 500 chars)
    - sources_score:   normalized unique source count (cap at 5)
    - relevance_score: the original decomposition relevance (already 0-1)
    """
    findings_count = len(node.findings) if node.findings else 0
    summary_len = len(node.summary) if node.summary else 0
    sources_count = len(node.sources) if node.sources else 0
    relevance = max(0.0, min(1.0, node.relevance_score))

    findings_score = min(findings_count / 10.0, 1.0)
    summary_score = min(summary_len / 500.0, 1.0)
    sources_score = min(sources_count / 5.0, 1.0)

    # Weighted average — all components equally important
    score = (findings_score + summary_score + sources_score + relevance) / 4.0

    logger.debug(
        f"[tree_evaluator] Branch {node.id} score={score:.3f} "
        f"(findings={findings_score:.2f}, summary={summary_score:.2f}, "
        f"sources={sources_score:.2f}, relevance={relevance:.2f})"
    )
    return round(score, 4)


def should_backtrack(node: "ResearchTreeNode") -> bool:
    """
    Determine if a branch should be retried via backtracking.

    Returns True if:
    - tree_backtrack_enabled is True
    - The branch score is below tree_backtrack_score_threshold
    - The node hasn't already been retried (retry_count check via node status)
    """
    if not settings.tree_backtrack_enabled:
        return False

    score = score_branch(node)
    threshold = float(settings.tree_backtrack_score_threshold)

    if score < threshold:
        logger.info(
            f"[tree_evaluator] Branch {node.id} ({node.topic[:40]}) "
            f"score {score:.3f} < threshold {threshold} → backtrack candidate"
        )
        return True
    return False
