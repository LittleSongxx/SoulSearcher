"""
Centralized LLM Factory for Weaver.

Provides unified model initialization across all modules.
Replaces duplicated _chat_model() functions.
"""

import json
import logging
from typing import Any, Dict, Optional

from langchain_openai import ChatOpenAI

from common.config import settings

logger = logging.getLogger(__name__)


def create_chat_model_params(
    model: str,
    temperature: float,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build ChatOpenAI parameters honoring model-specific app-config overrides."""
    llm_cfg = settings.llm_config_for_model(model)
    base_url = (
        llm_cfg.base_url if llm_cfg and llm_cfg.base_url else settings.openai_base_url
    )
    if llm_cfg:
        api_key = llm_cfg.api_key or ""
        if not api_key:
            if base_url and "dashscope.aliyuncs.com" in base_url.lower():
                api_key = settings.dashscope_api_key
            elif not llm_cfg.base_url or (
                base_url and base_url == settings.openai_base_url
            ):
                api_key = settings.openai_api_key
    else:
        api_key = settings.openai_api_key

    params: Dict[str, Any] = {
        "temperature": temperature,
        "model": model,
        "api_key": api_key,
        "timeout": settings.openai_timeout or None,
    }

    if settings.use_azure and not llm_cfg:
        params.update(
            {
                "azure_endpoint": settings.azure_endpoint or None,
                "azure_deployment": model,
                "api_version": settings.azure_api_version or None,
                "api_key": settings.azure_api_key or settings.openai_api_key,
            }
        )
    elif base_url:
        params["base_url"] = base_url

    merged_extra: Dict[str, Any] = {}
    if settings.openai_extra_body:
        try:
            merged_extra.update(json.loads(settings.openai_extra_body))
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in openai_extra_body; ignoring.")
    if extra_body:
        merged_extra.update(extra_body)
    if merged_extra:
        params["extra_body"] = merged_extra

    return params


def create_chat_model(
    model: str,
    temperature: float,
    extra_body: Optional[Dict[str, Any]] = None,
) -> ChatOpenAI:
    """
    Create a ChatOpenAI instance with proper configuration.

    Handles:
    - OpenAI API
    - Azure OpenAI
    - Custom base URLs
    - Extra body parameters

    Args:
        model: Model name/deployment
        temperature: Sampling temperature
        extra_body: Optional extra parameters for the API call

    Returns:
        Configured ChatOpenAI instance
    """
    return ChatOpenAI(
        **create_chat_model_params(model, temperature, extra_body=extra_body)
    )


def create_summary_model() -> ChatOpenAI:
    """
    Create a ChatOpenAI instance for message summarization.

    Uses settings.summary_messages_model or falls back to primary_model.
    Always uses temperature=0 for deterministic summarization.
    """
    model = settings.summary_messages_model or settings.primary_model
    return create_chat_model(model, temperature=0)


# Aliases for backward compatibility
build_chat_model = create_chat_model
