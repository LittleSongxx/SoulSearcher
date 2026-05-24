"""GAIA Mode — Short-answer research pipeline for benchmark evaluation.

GAIA (General AI Assistants) evaluates agents on multi-step reasoning tasks
requiring web search, tool use, and information synthesis.  Unlike Weaver's
default deep-research pipeline (which produces long-form cited reports), GAIA
expects concise, factual answers (a string, number, or list).

This module adds a GAIA-compatible execution path: skip clarify/brief/classify,
run the supervisor+researcher pipeline, then output a short answer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from agent.core.configuration import ResearchConfiguration
from agent.core.model_routing import configurable_model
from agent.core.prompts import resolve_prompt

logger = logging.getLogger(__name__)

GAIA_ANSWER_PROMPT = """You are answering a benchmark question.  Based on the research
findings below, produce a short, precise answer.

Rules:
- If the answer is a number, output ONLY the number.
- If the answer is a name or phrase, output ONLY that name/phrase.
- If the answer is a list, output comma-separated values.
- Do NOT add explanations, commentary, or markdown formatting.
- If you cannot determine the answer from the findings, output "I don't know".

<Research Question>
{question}
</Research Question>

<Research Findings>
{findings}
</Research Findings>

Answer:"""


async def gaia_answer_node(state: dict, config: RunnableConfig) -> dict:
    """Generate a concise GAIA-format answer from collected research notes.

    Called after the supervisor completes, replacing final_report_generation
    when running in GAIA benchmark mode.
    """
    research_config = ResearchConfiguration.from_runnable_config(config)

    notes = state.get("notes", [])
    research_brief = state.get("research_brief", "")

    findings = "\n\n".join(notes) if notes else "No research findings."

    model_config = {
        "model": research_config.get_model_for_task("result_synthesis"),
        "max_tokens": 512,
        "temperature": 0.0,
        "tags": ["langsmith:nostream"],
    }

    response = await configurable_model.with_config(model_config).ainvoke([
        HumanMessage(content=GAIA_ANSWER_PROMPT.format(
            question=research_brief,
            findings=findings,
        )),
    ])

    answer = (response.content if hasattr(response, "content") else str(response)).strip()

    logger.info(f"[GAIA] Answer: {answer[:200]}")

    return {
        "final_report": answer,
        "messages": [AIMessage(content=answer)],
    }
