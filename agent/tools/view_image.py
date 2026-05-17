"""View Image Tool — multimodal support based on deer-flow's view_image_tool.

Reads image files, validates format/size, encodes to base64, and stores in
state.viewed_images for injection into LLM vision context.

Adapted from: deer-flow/packages/harness/deerflow/tools/builtins/view_image_tool.py
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",  # Needs offset check: data[8:12] == b"WEBP"
    b"GIF8": "image/gif",
}


class ViewImageInput(BaseModel):
    """Input schema for the view_image tool."""
    image_path: str = Field(
        description="Absolute path to the image file. Supported formats: jpg, jpeg, png, webp, gif."
    )


def _detect_image_mime(image_data: bytes) -> str | None:
    """Detect MIME type from magic bytes."""
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_data) >= 12 and image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    if image_data.startswith(b"GIF8"):
        return "image/gif"
    return None


def _normalize_image_path(image_path: str) -> str:
    """Resolve and normalize the image path."""
    path = Path(os.path.expanduser(image_path)).resolve()
    return str(path)


async def view_image(
    image_path: str,
    *,
    _state: dict | None = None,
) -> dict:
    """Read an image file and return base64-encoded data for multimodal LLM input.

    This is the Weaver-adapted version of deer-flow's view_image_tool.
    It can be called both as a LangChain tool and directly from nodes.

    Args:
        image_path: Path to the image file.
        _state: Optional state dict for storing viewed_images (injected by node).

    Returns:
        dict with keys: success (bool), image_path (str), base64 (str),
             mime_type (str), error (str|None), state_update (dict|None)
    """
    result = {
        "success": False,
        "image_path": image_path,
        "base64": "",
        "mime_type": "",
        "error": None,
        "state_update": None,
    }

    normalized = _normalize_image_path(image_path)
    path = Path(normalized)

    # Validate extension
    ext = path.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        result["error"] = (
            f"Unsupported image format: {ext}. "
            f"Supported: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )
        return result

    expected_mime = _EXTENSION_TO_MIME.get(ext)

    # Check file exists
    if not path.exists():
        result["error"] = f"Image file not found: {image_path}"
        return result

    if not path.is_file():
        result["error"] = f"Path is not a file: {image_path}"
        return result

    # Check size
    try:
        image_size = path.stat().st_size
    except OSError as e:
        result["error"] = f"Cannot read image metadata: {e}"
        return result

    if image_size > _MAX_IMAGE_BYTES:
        result["error"] = (
            f"Image too large: {image_size} bytes. "
            f"Maximum: {_MAX_IMAGE_BYTES} bytes (20MB)"
        )
        return result

    # Read and validate
    try:
        image_data = path.read_bytes()
    except Exception as e:
        result["error"] = f"Failed to read image file: {e}"
        return result

    # Detect actual MIME from magic bytes
    detected_mime = _detect_image_mime(image_data)
    if detected_mime is None:
        result["error"] = "File contents do not match a supported image format"
        return result

    if detected_mime != expected_mime:
        result["error"] = (
            f"Image content is {detected_mime} but extension indicates {expected_mime}"
        )
        return result

    # Encode
    image_base64 = base64.b64encode(image_data).decode("utf-8")

    logger.info(
        f"[ViewImage] Loaded image: {image_path} "
        f"({detected_mime}, {image_size:,} bytes)"
    )

    result["success"] = True
    result["base64"] = image_base64
    result["mime_type"] = detected_mime

    # Build state update for viewed_images
    if _state is not None:
        existing = _state.get("viewed_images", {})
        existing[image_path] = {"base64": image_base64, "mime_type": detected_mime}
        result["state_update"] = {"viewed_images": existing}

    return result


# =============================================================================
# LangChain Tool Adapter
# =============================================================================


async def view_image_tool(image_path: str) -> str:
    """LangChain tool: Read an image file and make it available for vision analysis.

    Use this tool when you need to view or analyze an image file.
    The image will be encoded as base64 and available for the next LLM call.

    Args:
        image_path: Absolute path to the image file (jpg, jpeg, png, webp, gif).
    """
    result = await view_image(image_path)
    if result["success"]:
        return (
            f"Successfully loaded image: {image_path}\n"
            f"Format: {result['mime_type']}\n"
            f"Base64 length: {len(result['base64'])} chars"
        )
    else:
        return f"Error viewing image: {result['error']}"
