from __future__ import annotations

from typing import Any, Optional

import httpx


async def fetch_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    try:
        response = await client.get(path, timeout=timeout_s)
        if response.status_code != 200:
            return {"_collector_error": f"HTTP {response.status_code}: {response.text[:500]}"}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}
    except Exception as exc:
        return {"_collector_error": str(exc)}


async def fetch_run_metrics(
    client: httpx.AsyncClient,
    thread_id: Optional[str],
) -> dict[str, Any]:
    if not thread_id:
        return {"_collector_error": "missing thread_id"}
    return await fetch_json(client, f"/api/runs/{thread_id}")


async def fetch_evidence(
    client: httpx.AsyncClient,
    thread_id: Optional[str],
) -> dict[str, Any]:
    if not thread_id:
        return {"_collector_error": "missing thread_id"}
    return await fetch_json(client, f"/api/sessions/{thread_id}/evidence")


def extract_final_report_from_event(event_name: str, data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if event_name in {"completion", "message"}:
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            return content
    if event_name == "artifact" and data.get("type") == "report":
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def append_text_delta(existing: str, event_name: str, data: Any) -> str:
    if event_name != "text" or not isinstance(data, dict):
        return existing
    content = data.get("content")
    if isinstance(content, str):
        return existing + content
    return existing
