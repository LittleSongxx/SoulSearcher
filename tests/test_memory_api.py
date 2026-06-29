from __future__ import annotations

import asyncio
import pytest
from types import SimpleNamespace


def _request():
    return SimpleNamespace(state=SimpleNamespace(principal_id=""))


def test_memory_api_reports_unavailable_backend(monkeypatch):
    import main
    from agent.memory import MemoryUnavailableError, set_memory_service
    import agent.memory.service as memory_service_module

    def unavailable_service():
        raise MemoryUnavailableError("memory unavailable")

    monkeypatch.setattr(main.settings, "memory_enabled", True)
    monkeypatch.setattr(memory_service_module, "create_memory_service", unavailable_service)
    set_memory_service(None)

    status = asyncio.run(main.memory_status())
    with pytest.raises(main.HTTPException) as exc_info:
        asyncio.run(main.list_memory_records(_request()))

    assert status["available"] is False
    assert exc_info.value.status_code == 503


def test_memory_api_lists_records_with_injected_service(monkeypatch):
    import main
    from agent.memory import InMemoryMemoryStore, MemoryRecord, MemoryService, set_memory_service

    service = MemoryService(InMemoryMemoryStore(), embedding_dim=8)
    service.upsert_record(
        MemoryRecord(user_id="default_user", content="Weaver unified memory smoke record.")
    )
    set_memory_service(service)
    monkeypatch.setattr(main.settings, "memory_enabled", True)

    response = asyncio.run(main.list_memory_records(_request()))

    assert response["count"] == 1
    assert response["records"][0]["content"] == "Weaver unified memory smoke record."
    set_memory_service(None)
