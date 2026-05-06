"""
Customer Support Agent with Mem0 memory.

Provides a simple LangGraph that:
- Retrieves relevant memories for the user
- Adds a system prompt with context
- Stores the interaction back to memory
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agent.core.llm_factory import create_chat_model
from common.config import settings
from tools.core.memory_client import fetch_memories, store_interaction


class SupportState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str


def _support_model() -> ChatOpenAI:
    return create_chat_model(settings.primary_model, temperature=0.3)


def support_node(state: SupportState):
    messages = state["messages"]
    user_id = state.get("user_id") or settings.memory_user_id
    last_user_message = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_user_message = msg.content
            break

    # Retrieve memories
    mem_entries = fetch_memories(query=last_user_message, user_id=user_id)
    context = ""
    if mem_entries:
        context = "Relevant information from previous conversations:\n" + "\n".join(
            f"- {m}" for m in mem_entries
        )

    system_prompt = "You are a helpful customer support assistant. Use provided context to personalize and remember user preferences."
    if context:
        system_prompt += f"\n{context}"

    llm = _support_model()
    response = llm.invoke([SystemMessage(content=system_prompt)] + messages)
    reply_text = response.content if hasattr(response, "content") else str(response)

    # Store interaction
    if last_user_message:
        store_interaction(last_user_message, reply_text, user_id=user_id)

    return {"messages": [AIMessage(content=reply_text)]}


def create_support_graph(checkpointer=None, store=None):
    graph = StateGraph(SupportState)
    graph.add_node("support", support_node)
    graph.add_edge(START, "support")
    graph.add_edge("support", END)
    return graph.compile(checkpointer=checkpointer, store=store)
