"""Three-tier Model Routing for Deep Research Agent.

Configurable model used by all graph nodes to dynamically select
the appropriate LLM at runtime based on RunnableConfig.

Usage:
    from agent.core.model_routing import configurable_model
    llm = configurable_model.with_config({"model": "gpt-4.1", "max_tokens": 4096})
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model

configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key"),
)
