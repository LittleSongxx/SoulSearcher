"""
Research Coordinator Agent.

Inspired by DeerFlow's Coordinator pattern.
Orchestrates the research workflow, deciding when to gather more info vs. synthesize.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


class CoordinatorAction(str, Enum):
    """Actions the coordinator can take."""

    PLAN = "plan"  # Generate/refine research plan
    RESEARCH = "research"  # Gather more information
    SYNTHESIZE = "synthesize"  # Synthesize findings into report
    REFLECT = "reflect"  # Reflect on progress and strategy
    COMPLETE = "complete"  # Research is complete


COORDINATOR_PROMPT = """
# 角色
你是一名研究协调者，负责管理整个研究流程。你需要根据当前研究状态做出关键决策。

# 当前研究状态
- 主题: {topic}
- 已完成查询数: {num_queries}
- 已收集来源数: {num_sources}
- 已生成摘要数: {num_summaries}
- 当前轮次: {current_epoch}/{max_epochs}
- 质量总分: {quality_score}
- 缺口数量: {quality_gap_count}
- 引用准确/覆盖: {citation_accuracy}
- 已有报告: {has_report}
- 已知信息摘要: {knowledge_summary}

# 你可以选择的行动
1. **plan**: 生成或优化研究计划（适用于研究初期或发现新方向时）
2. **research**: 继续收集更多信息（适用于信息不足时）
3. **synthesize**: 综合已有发现生成报告（适用于已有足够来源但还没生成报告时）
4. **reflect**: 反思当前进展和策略（适用于进展缓慢或方向不明时）
5. **complete**: 完成研究（**仅当** 已有高质量报告时才选择此项）

# 重要规则
- 如果 已收集来源数 == 0，你**必须**选择 plan 或 research
- 如果 已有报告 == False 且 已收集来源数 > 0，你**应该**选择 synthesize
- 只有当 已有报告 == True 且 质量总分 >= 0.7 时，才可以选择 complete
- 当前轮次 接近 最大轮次 时，优先选择 synthesize 来确保产出报告

# 决策要求
根据当前状态选择最合适的下一步行动，并给出理由。

# 输出格式
严格按照以下格式输出（每项占一行）：
action: <行动名称>
reasoning: <决策理由>
priority_topics: <如选择research，列出优先研究的子话题，逗号分隔>
"""


@dataclass
class CoordinatorDecision:
    """Decision made by the coordinator."""

    action: CoordinatorAction
    reasoning: str
    priority_topics: List[str]


class ResearchCoordinator:
    """
    Coordinates the research workflow.

    Decides the next step based on current research state:
    - How much information has been collected
    - Quality and coverage of existing findings
    - Research budget (epochs remaining)
    """

    def __init__(self, llm: BaseChatModel, config: Dict[str, Any] = None):
        self.llm = llm
        self.config = config or {}

    def decide_next_action(
        self,
        topic: str,
        num_queries: int,
        num_sources: int,
        num_summaries: int,
        current_epoch: int,
        max_epochs: int,
        knowledge_summary: str = "",
        quality_score: Optional[float] = None,
        quality_gap_count: int = 0,
        citation_accuracy: Optional[float] = None,
        has_report: bool = False,
    ) -> CoordinatorDecision:
        """
        Decide the next action based on current research state.

        Returns:
            CoordinatorDecision with the chosen action
        """
        # ── Deterministic rules (no LLM needed) ──

        # Rule 0: No queries yet → must plan
        if num_queries == 0 and num_sources == 0:
            return CoordinatorDecision(
                action=CoordinatorAction.PLAN,
                reasoning="研究尚未开始，需要生成研究计划",
                priority_topics=[],
            )

        # Rule 1: Max epochs reached → synthesize if missing report, else complete
        if current_epoch >= max_epochs:
            if not has_report and num_sources > 0:
                return CoordinatorDecision(
                    action=CoordinatorAction.SYNTHESIZE,
                    reasoning="已达到最大研究轮次且尚无报告，进入综合阶段",
                    priority_topics=[],
                )
            return CoordinatorDecision(
                action=CoordinatorAction.COMPLETE,
                reasoning="已达到最大研究轮次，完成研究",
                priority_topics=[],
            )

        quality_score = None if quality_score is None else float(quality_score)
        citation_accuracy = (
            None if citation_accuracy is None else float(citation_accuracy)
        )
        quality_gap_count = max(0, int(quality_gap_count or 0))

        # Rule 2: Have sources, no report → synthesize
        if num_sources > 0 and not has_report:
            return CoordinatorDecision(
                action=CoordinatorAction.SYNTHESIZE,
                reasoning="已有搜索来源但尚未生成报告，进入综合阶段",
                priority_topics=[],
            )

        # Rule 3: Quality-driven completion — only if report exists
        if (
            has_report
            and quality_score is not None
            and num_summaries > 0
            and quality_score >= 0.82
            and quality_gap_count == 0
            and (citation_accuracy is None or citation_accuracy >= 0.7)
        ):
            return CoordinatorDecision(
                action=CoordinatorAction.COMPLETE,
                reasoning="报告已生成且质量评估良好、无明显缺口，完成研究流程",
                priority_topics=[],
            )

        # Rule 4: Low quality with report → need more research
        if (
            has_report
            and quality_score is not None
            and (
                quality_score < 0.6
                or quality_gap_count > 0
                or (citation_accuracy is not None and citation_accuracy < 0.55)
            )
        ):
            return CoordinatorDecision(
                action=CoordinatorAction.RESEARCH,
                reasoning="质量信号显示仍存在证据或覆盖缺口，继续补充研究",
                priority_topics=[],
            )

        # Rule 5: Penultimate epoch without report → synthesize
        if current_epoch >= max_epochs - 1 and not has_report and num_sources > 0:
            return CoordinatorDecision(
                action=CoordinatorAction.SYNTHESIZE,
                reasoning="即将达到最大轮次且尚无报告，优先综合",
                priority_topics=[],
            )

        # ── LLM for remaining ambiguous decisions ──
        prompt = ChatPromptTemplate.from_messages([("user", COORDINATOR_PROMPT)])

        msg = prompt.format_messages(
            topic=topic,
            num_queries=num_queries,
            num_sources=num_sources,
            num_summaries=num_summaries,
            current_epoch=current_epoch,
            max_epochs=max_epochs,
            quality_score=(
                f"{quality_score:.2f}" if quality_score is not None else "unknown"
            ),
            quality_gap_count=quality_gap_count,
            citation_accuracy=(
                f"{citation_accuracy:.2f}"
                if citation_accuracy is not None
                else "unknown"
            ),
            has_report="True" if has_report else "False",
            knowledge_summary=knowledge_summary[:2000] or "暂无",
        )

        response = self.llm.invoke(msg, config=self.config)
        content = getattr(response, "content", "") or ""

        return self._parse_decision(content)

    def _parse_decision(self, content: str) -> CoordinatorDecision:
        """Parse the coordinator's decision from LLM output."""
        action = CoordinatorAction.RESEARCH  # default
        reasoning = ""
        priority_topics = []

        for line in content.strip().split("\n"):
            line = line.strip()
            if line.lower().startswith("action:"):
                action_str = line.split(":", 1)[1].strip().lower()
                try:
                    action = CoordinatorAction(action_str)
                except ValueError:
                    action = CoordinatorAction.RESEARCH
            elif line.lower().startswith("reasoning:"):
                reasoning = line.split(":", 1)[1].strip()
            elif line.lower().startswith("priority_topics:"):
                topics_str = line.split(":", 1)[1].strip()
                priority_topics = [
                    t.strip() for t in topics_str.split(",") if t.strip()
                ]

        return CoordinatorDecision(
            action=action,
            reasoning=reasoning,
            priority_topics=priority_topics,
        )
