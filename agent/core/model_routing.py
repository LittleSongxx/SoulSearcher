"""Three-tier Model Routing for Deep Research Agent.

Configurable model used by all graph nodes to dynamically select
the appropriate LLM at runtime based on RunnableConfig.

Usage:
    from agent.core.model_routing import configurable_model
    llm = configurable_model.with_config({"model": "gpt-4.1", "max_tokens": 4096})
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model

from agent.core.llm_factory import create_chat_model_params

configurable_model = init_chat_model(
    configurable_fields=(
        "model",
        "max_tokens",
        "api_key",
        "base_url",
        "timeout",
        "temperature",
        "extra_body",
        "azure_endpoint",
        "azure_deployment",
        "api_version",
    ),
)


def build_model_config(
    *,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    tags: list[str] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a RunnableConfig model override using the centralized LLM factory."""
    params = create_chat_model_params(
        model=model,
        temperature=0.0 if temperature is None else temperature,
        extra_body=extra_body,
    )
    config: dict[str, Any] = {
        key: value
        for key, value in params.items()
        if key in {
            "model",
            "api_key",
            "base_url",
            "timeout",
            "temperature",
            "extra_body",
            "azure_endpoint",
            "azure_deployment",
            "api_version",
        }
        and value is not None
    }
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    if temperature is None:
        config.pop("temperature", None)
    if tags:
        config["tags"] = tags
    return config
