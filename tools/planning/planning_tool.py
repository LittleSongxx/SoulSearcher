
from langchain_core.tools import tool

from agent.core.llm_factory import create_chat_model
from common.config import settings
from prompts.planning import PLANNING_SYSTEM_PROMPT


@tool
def plan_steps(goal: str, max_steps: int = 5) -> list[str]:
    """
    Generate 3-7 concise steps to achieve the goal using the reasoning model.
    """
    llm = create_chat_model(
        settings.reasoning_model or settings.primary_model, temperature=0.2
    )
    messages = [
        {"role": "system", "content": PLANNING_SYSTEM_PROMPT.strip()},
        {
            "role": "user",
            "content": f"Goal: {goal}\nReturn up to {max_steps} steps as a numbered list.",
        },
    ]
    resp = llm.invoke(messages)
    content = getattr(resp, "content", "") or ""
    steps: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # strip numbering / bullets
        if line[0].isdigit():
            line = line.split(" ", 1)[-1]
        line = line.lstrip("-").strip()
        if line and len(steps) < max_steps:
            steps.append(line)
    return steps[:max_steps] if steps else [goal]
