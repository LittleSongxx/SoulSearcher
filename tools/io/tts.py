"""
DashScope TTS (Text-to-Speech) using CosyVoice models.
Hard-requires the DashScope SDK and a valid `DASHSCOPE_API_KEY`.
"""

import base64
import json
import logging
import os
import re
import time
from importlib import metadata
from typing import Any, Optional

import dashscope
from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

logger = logging.getLogger(__name__)

# Available voices (simplified descriptions kept ASCII for clarity)
AVAILABLE_VOICES = {
    "longanyang": "Longanyang (male, Mandarin)",
    "longxiaochun": "Longxiaochun (female, Mandarin)",
    "longwan": "Longwan (female, warm)",
    "longyue": "Longyue (female, lively)",
    "longfei": "Longfei (male, magnetic)",
    "longjielidou": "Longjielidou (boy)",
    "longshuo": "Longshuo (male, deep)",
    "longshu": "Longshu (female, wise)",
    "loongstella": "Stella (female, English)",
    "loongbella": "Bella (female, English)",
}

DEFAULT_VOICE = "longxiaochun"
# Default to a broadly compatible model. In environments with older DashScope SDKs
# or restricted accounts, newer CosyVoice variants may return TaskFailed/InvalidParameter.
DEFAULT_MODEL = "cosyvoice-v1"
DEFAULT_AUDIO_FORMAT = AudioFormat.MP3_22050HZ_MONO_256KBPS


def _is_auth_error_message(message: str) -> bool:
    text = (message or "").lower()
    return (
        "invalidapikey" in text
        or "unauthorized" in text
        or "handshake status 401" in text
        or "authentication" in text
    )


def _extract_error_json(message: str) -> Optional[dict[str, Any]]:
    """
    Best-effort extraction of JSON error payloads from DashScope websocket errors.

    Example substring:
      b'{"code":"InvalidApiKey","message":"Invalid API-key provided.","request_id":"..."}'
    """
    if not message:
        return None
    candidates = []

    # bytes-literal payload
    m = re.search(r"b'(\{.*?\})'", message)
    if m:
        candidates.append(m.group(1))

    # raw JSON object (fallback)
    m2 = re.search(r"(\{\\?\"code\\?\".*?\})", message)
    if m2:
        candidates.append(m2.group(1))

    for raw in candidates:
        try:
            raw = raw.replace("\\\"", "\"")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


def _summarize_error(exc: Exception) -> str:
    text = str(exc).strip()
    payload = _extract_error_json(text)
    if isinstance(payload, dict):
        code = str(payload.get("code") or "").strip()
        msg = str(payload.get("message") or "").strip()
        if code and msg:
            return f"{code}: {msg}"
        if msg:
            return msg
    # Keep messages short for API responses.
    if len(text) > 300:
        return text[:300] + "..."
    return text or exc.__class__.__name__


def _validate_dashscope_api_key(api_key: str) -> Optional[str]:
    """
    Validate DashScope credentials via a lightweight HTTP call.

    This avoids relying on websocket error propagation (which can surface as a
    generic "Connection is already closed.").

    Returns:
        None when the key appears valid, otherwise a short "CODE: message" string.
    """
    key = (api_key or "").strip()
    if not key:
        return "No API key configured"
    try:
        from http import HTTPStatus

        from dashscope import Models  # type: ignore

        resp = Models.list(page=1, page_size=1, api_key=key)
        status = int(resp.get("status_code") or 0)
        if status == int(HTTPStatus.OK):
            return None
        code = str(resp.get("code") or "").strip()
        msg = str(resp.get("message") or "").strip()
        if code and msg:
            return f"{code}: {msg}"
        return msg or f"DashScope API error (status={status})"
    except Exception as e:
        return _summarize_error(e)


class TTSService:
    """Text-to-speech service powered by DashScope."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if not self.api_key:
            self.enabled = False
            logger.warning("DASHSCOPE_API_KEY not set. TTS service disabled.")
            return

        dashscope.api_key = self.api_key
        self.enabled = True
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[float] = None
        self._warn_if_old_sdk()

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        model: str = DEFAULT_MODEL,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> dict[str, Any]:
        """Run TTS and return a base64-encoded MP3 payload."""
        if not text or not text.strip():
            raise ValueError("Text cannot be empty.")

        max_length = 2000
        if len(text) > max_length:
            text = text[:max_length] + "..."
            logger.warning("Text truncated to %s characters", max_length)

        if voice not in AVAILABLE_VOICES:
            logger.warning(
                "Requested voice '%s' not in AVAILABLE_VOICES; proceeding anyway.", voice
            )

        audio_data = None
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                # Explicitly set an audio format. DashScope SDK versions around 1.20.x
                # can send an invalid "Default"/0 format when using AudioFormat.DEFAULT,
                # which causes the engine to fail with InvalidParameter.
                synthesizer = SpeechSynthesizer(
                    model=model,
                    voice=voice,
                    format=DEFAULT_AUDIO_FORMAT,
                )
                audio_data = synthesizer.call(text)
                if audio_data:
                    break
                last_error = RuntimeError("No audio data returned from DashScope.")
            except Exception as exc:  # noqa: PERF203
                # Preserve the most informative error (handshake/auth errors often appear only once).
                exc_text = str(exc)
                if last_error is None:
                    last_error = exc
                elif "connection is already closed" in exc_text.lower() and _is_auth_error_message(
                    str(last_error)
                ):
                    # Don't overwrite an auth/handshake error with a generic close error.
                    pass
                else:
                    last_error = exc

                summary = _summarize_error(exc)
                logger.warning("TTS attempt %s/%s failed: %s", attempt, max_retries, summary)

                # DashScope websocket auth failures sometimes surface as a generic close error.
                # If that happens, validate the key once and fail fast instead of retrying.
                if attempt == 1 and "connection is already closed" in exc_text.lower():
                    validated = _validate_dashscope_api_key(self.api_key)
                    if validated:
                        last_error = RuntimeError(validated)
                        break

                # Auth errors won't succeed on retry; fail fast.
                if _is_auth_error_message(exc_text):
                    break
            if attempt < max_retries:
                time.sleep(retry_delay)

        if not audio_data:
            summary = _summarize_error(last_error or RuntimeError("Unknown error"))
            if "connection is already closed" in summary.lower():
                validated = _validate_dashscope_api_key(self.api_key)
                if validated:
                    summary = validated
            self.last_error = summary
            self.last_error_time = time.time()
            raise RuntimeError(f"TTS failed: {self.last_error}")

        audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        logger.info("TTS success: %s chars -> %s bytes", len(text), len(audio_data))
        self.last_error = None
        self.last_error_time = None

        return {
            "success": True,
            "audio": audio_base64,
            "format": "mp3",
            "voice": voice,
            "text_length": len(text),
            "error": None,
        }

    def get_available_voices(self) -> dict[str, str]:
        return AVAILABLE_VOICES.copy()

    @staticmethod
    def _warn_if_old_sdk(min_version: str = "1.24.6") -> None:
        """Log a warning if the installed dashscope SDK is older than recommended."""
        try:
            current = metadata.version("dashscope")
            if tuple(int(x) for x in current.split(".")[:3]) < tuple(
                int(x) for x in min_version.split(".")
            ):
                logger.warning(
                    "dashscope %s detected; TTS works best with >= %s", current, min_version
                )
        except Exception:
            # Version introspection is best-effort only
            pass


# Global TTS service instance (lazy-init)
_tts_service: Optional[TTSService] = None


def get_tts_service(api_key: Optional[str] = None) -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService(api_key)
    return _tts_service


def init_tts_service(api_key: str) -> TTSService:
    global _tts_service
    _tts_service = TTSService(api_key)
    return _tts_service
