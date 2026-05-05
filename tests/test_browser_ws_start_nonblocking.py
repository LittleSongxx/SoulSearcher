import asyncio
import json
from concurrent.futures import TimeoutError as FutureTimeout

import main


def _receive_json_with_timeout(ws, *, timeout_s: float = 1.0):
    fut = ws.portal.start_task_soon(ws._send_rx.receive)
    try:
        message = fut.result(timeout=timeout_s)
    except FutureTimeout:
        # Best-effort: close to unblock the pending receive task.
        try:
            ws.close()
        except Exception:
            pass
        try:
            fut.result(timeout=2.0)
        except Exception:
            pass
        raise AssertionError("Timed out waiting for WebSocket message")

    ws._raise_on_close(message)
    return json.loads(message["text"])


class _DummyPage:
    url = "about:blank"

    def title(self):
        return "Dummy"

    def set_content(self, _html):
        return None

    def screenshot(self, **_kwargs):
        return b"fake_jpeg_bytes"


class _DummyBrowserSession:
    def __init__(self):
        self.page = _DummyPage()

    def get_page(self):
        return self.page

    def start_screencast(self, **_kwargs):
        return True

    def stop_screencast(self):
        return None


class _SlowSandboxBrowserSessions:
    """
    Simulate a cold-starting sandbox by making run_async slow.

    The WS endpoint should still acknowledge start immediately, instead of
    blocking on sandbox initialization.
    """

    def __init__(self, delay_s: float = 5.0):
        self.session = _DummyBrowserSession()
        self.delay_s = float(delay_s)

    def get(self, _thread_id: str):
        return self.session

    async def run_async(self, thread_id: str, fn, *args, **kwargs):
        _ = thread_id
        await asyncio.sleep(self.delay_s)
        return fn(*args, **kwargs)

    def peek_screencast_frame(self, _thread_id: str):
        return None


def test_browser_stream_ws_start_is_nonblocking_during_sandbox_cold_start(monkeypatch):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "")
    monkeypatch.setitem(main.settings.__dict__, "e2b_api_key", "e2b_test_key")
    monkeypatch.setitem(main.settings.__dict__, "sandbox_template_browser", "sandbox_template_test_browser")

    dummy = _SlowSandboxBrowserSessions(delay_s=10.0)
    monkeypatch.setattr(main, "sandbox_browser_sessions", dummy)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    thread_id = "thread_test_ws_start_nonblocking"

    with client.websocket_connect(f"/api/browser/{thread_id}/stream") as ws:
        initial = _receive_json_with_timeout(ws, timeout_s=1.0)
        assert initial["type"] == "status"

        ws.send_json({"action": "start", "quality": 70, "max_fps": 10})
        started = _receive_json_with_timeout(ws, timeout_s=0.75)
        assert started["type"] == "status"
        assert started["message"] == "Screencast started"

        frame = _receive_json_with_timeout(ws, timeout_s=0.75)
        assert frame["type"] == "frame"
        assert frame["source"] == "placeholder"
        assert frame.get("metadata", {}).get("placeholder") is True

