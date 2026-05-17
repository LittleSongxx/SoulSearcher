"""Memory update prompts for LLM extraction."""

MEMORY_UPDATE_PROMPT = """You are a memory manager. Analyze the conversation between a user and an AI agent, and produce a JSON memory update.

Current memory state:
{current_memory}

Recent conversation:
{conversation}

Produce a JSON object with these keys:
- user: {{workContext, personalContext, topOfMind}} — each with shouldUpdate (bool) and summary (str)
- history: {{recentMonths, earlierContext, longTermBackground}} — each with shouldUpdate (bool) and summary (str)
- factsToRemove: list of fact IDs that are now incorrect or outdated
- newFacts: list of new facts with {{content, category, confidence}}

Fact categories: preference, knowledge, context, behavior, goal
Confidence: 0.0-1.0 (how certain you are)

Return ONLY valid JSON, no other text."""


def format_conversation_for_update(messages: list) -> str:
    """Format conversation messages for the memory update prompt."""
    lines: list[str] = []
    for msg in messages[-20:]:  # Only last 20 messages
        role = "user"
        if hasattr(msg, "type"):
            if msg.type == "ai":
                role = "assistant"
            elif msg.type == "tool":
                continue  # Skip tool messages
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(block.get("text", ""))
            content = "\n".join(parts)
        content = str(content or "").strip()
        if content:
            lines.append(f"{role}: {content[:500]}")  # Truncate per message
    return "\n".join(lines)
