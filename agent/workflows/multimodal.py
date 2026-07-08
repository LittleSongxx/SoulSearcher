"""Multimodal Support — image injection for vision-capable LLM calls.

Adapted from deer-flow's ViewImageMiddleware pattern:
1. Detects view_image tool completions in conversation
2. Injects base64 image data as HumanMessage content blocks before next LLM call
3. Provides helpers for building multimodal message content from user images

Integrates with the research graph at key LLM call sites:
- clarify_with_user: inject user-uploaded images
- fixed-role agents: inject viewed_images after view_image tool use
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)


# =============================================================================
# Image Content Builder
# =============================================================================


def build_multimodal_content(
    text: str,
    images: list[dict[str, Any]] | None = None,
    viewed_images: dict[str, dict[str, str]] | None = None,
) -> str | list[dict]:
    """Build message content with optional image blocks for vision-capable LLMs.

    Pattern from deer-flow's ViewImageMiddleware._create_image_details_message().
    Returns plain text if no images, otherwise a mixed list with text + image_url parts.

    Args:
        text: The text content.
        images: List of image dicts from user input (may have 'url' or 'base64').
        viewed_images: Dict of path→{base64, mime_type} from view_image tool.

    Returns:
        str if no images, list[dict] if images present (for HumanMessage content).
    """
    parts: list[dict] = [{"type": "text", "text": text}]

    has_images = False

    # Handle user-uploaded images (from main.py input)
    normalized = _normalize_user_images(images or [])
    for img in normalized:
        has_images = True
        parts.append({
            "type": "image_url",
            "image_url": {"url": img["url"]},
        })

    # Handle view_image tool results
    if viewed_images:
        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "image/png")
            base64_data = image_data.get("base64", "")
            if base64_data:
                has_images = True
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                })

    if has_images:
        return parts
    return text


def _normalize_user_images(images: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize user-provided image payloads to data URLs.

    Normalize the same image payload shapes accepted by the API layer.
    Handles: base64 strings, data URLs, and file paths.
    """
    import base64
    import os
    import re

    from agent.tools.view_image import _detect_image_mime

    normalized = []
    for img in images or []:
        if not isinstance(img, dict):
            continue

        url = img.get("url", "") or img.get("data", "") or img.get("base64", "")

        if not url:
            continue

        # Already a data URL
        if url.startswith("data:image/"):
            normalized.append({"url": url})
            continue

        # Already a full URL
        if url.startswith("http://") or url.startswith("https://"):
            normalized.append({"url": url})
            continue

        # Might be a file path — try to load
        if os.path.isfile(url):
            try:
                path = os.path.abspath(url)
                with open(path, "rb") as f:
                    data = f.read()
                mime = _detect_image_mime(data) or "image/png"
                b64 = base64.b64encode(data).decode("utf-8")
                normalized.append({"url": f"data:{mime};base64,{b64}"})
                continue
            except Exception:
                pass

        # Assume base64 string — use provided mime_type or detect from magic bytes
        if re.match(r"^[A-Za-z0-9+/=]+$", url):
            user_mime = img.get("mime", "") or img.get("mime_type", "")
            if user_mime.startswith("image/"):
                mime = user_mime
            else:
                try:
                    raw = base64.b64decode(url)
                    mime = _detect_image_mime(raw) or "image/png"
                except Exception:
                    mime = "image/png"
            normalized.append({"url": f"data:{mime};base64,{url}"})
            continue

        # Last resort: treat as URL
        normalized.append({"url": url})

    return normalized


# =============================================================================
# View Image Completion Detector
# =============================================================================


def _get_last_assistant_message(messages: list) -> AIMessage | None:
    """Get the most recent AIMessage from the message list."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _has_view_image_tool_calls(message: AIMessage) -> bool:
    """Check if the assistant message contains view_image tool calls."""
    if not hasattr(message, "tool_calls") or not message.tool_calls:
        return False
    return any(
        tc.get("name") == "view_image"
        for tc in message.tool_calls
    )


def _all_view_image_tools_completed(
    messages: list, assistant_msg: AIMessage
) -> bool:
    """Check if all view_image tool calls have corresponding ToolMessages."""
    if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
        return False

    view_image_ids = {
        tc.get("id")
        for tc in assistant_msg.tool_calls
        if tc.get("name") == "view_image" and tc.get("id")
    }

    if not view_image_ids:
        return False

    try:
        assistant_idx = messages.index(assistant_msg)
    except ValueError:
        return False

    completed_ids = set()
    for msg in messages[assistant_idx + 1:]:
        if isinstance(msg, ToolMessage) and msg.tool_call_id:
            completed_ids.add(msg.tool_call_id)

    return view_image_ids.issubset(completed_ids)


def should_inject_images(messages: list) -> bool:
    """Determine if we should inject viewed images into the next LLM call.

    Pattern from deer-flow's ViewImageMiddleware._should_inject_image_message().

    Args:
        messages: The current message list (role messages).

    Returns:
        True if images should be injected.
    """
    if not messages:
        return False

    last_ai = _get_last_assistant_message(messages)
    if not last_ai:
        return False

    if not _has_view_image_tool_calls(last_ai):
        return False

    if not _all_view_image_tools_completed(messages, last_ai):
        return False

    # Check we haven't already injected
    try:
        ai_idx = messages.index(last_ai)
        for msg in messages[ai_idx + 1:]:
            if isinstance(msg, HumanMessage):
                content = str(msg.content)
                if "Here are the images" in content:
                    return False
    except ValueError:
        pass

    return True


def build_image_injection_message(
    viewed_images: dict[str, dict[str, str]],
) -> HumanMessage | None:
    """Build a HumanMessage with injected image content blocks.

    Pattern from deer-flow's ViewImageMiddleware._create_image_details_message().

    Args:
        viewed_images: Dict of path→{base64, mime_type}.

    Returns:
        HumanMessage with mixed text+image content, or None if no images.
    """
    if not viewed_images:
        return None

    content_blocks: list[dict] = [
        {"type": "text", "text": "Here are the images you've viewed:"}
    ]

    for image_path, image_data in viewed_images.items():
        mime_type = image_data.get("mime_type", "image/png")
        base64_data = image_data.get("base64", "")

        content_blocks.append({
            "type": "text",
            "text": f"\n- {image_path} ({mime_type})",
        })

        if base64_data:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
            })

    logger.info(f"[Multimodal] Injecting {len(viewed_images)} image(s) into LLM context")
    return HumanMessage(content=content_blocks)
