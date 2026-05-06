import uuid

import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.mark.asyncio
async def test_cancel_all_only_cancels_principal_tasks_when_internal_auth_enabled(
    monkeypatch,
):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")
    monkeypatch.setitem(main.settings.__dict__, "auth_user_header", "X-Weaver-User")

    suffix = uuid.uuid4().hex[:8]
    thread_alice = f"thread_alice_{suffix}"
    thread_bob = f"thread_bob_{suffix}"

    # Create active tokens (pending) for two users.
    await main.cancellation_manager.create_token(
        thread_alice, metadata={"user_id": "alice"}
    )
    await main.cancellation_manager.create_token(
        thread_bob, metadata={"user_id": "bob"}
    )

    # Bind thread ownership.
    main.set_thread_owner(thread_alice, "alice")
    main.set_thread_owner(thread_bob, "bob")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/research/cancel-all",
            headers={
                "Authorization": "Bearer test-key",
                "X-Weaver-User": "alice",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("cancelled_count") == 1

    # Alice task cancelled; Bob task untouched.
    alice_token = main.cancellation_manager.get_token(thread_alice)
    bob_token = main.cancellation_manager.get_token(thread_bob)
    assert alice_token is not None and alice_token.is_cancelled is True
    assert bob_token is not None and bob_token.is_cancelled is False

    # Cleanup the remaining token to avoid cross-test leakage.
    await main.cancellation_manager.cancel(thread_bob, "test cleanup")


@pytest.mark.asyncio
async def test_active_tasks_is_filtered_by_principal_when_internal_auth_enabled(
    monkeypatch,
):
    monkeypatch.setitem(main.settings.__dict__, "internal_api_key", "test-key")
    monkeypatch.setitem(main.settings.__dict__, "auth_user_header", "X-Weaver-User")

    suffix = uuid.uuid4().hex[:8]
    thread_alice = f"thread_alice_{suffix}"
    thread_bob = f"thread_bob_{suffix}"

    await main.cancellation_manager.create_token(
        thread_alice, metadata={"user_id": "alice"}
    )
    await main.cancellation_manager.create_token(
        thread_bob, metadata={"user_id": "bob"}
    )
    main.set_thread_owner(thread_alice, "alice")
    main.set_thread_owner(thread_bob, "bob")

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/tasks/active",
            headers={
                "Authorization": "Bearer test-key",
                "X-Weaver-User": "alice",
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    active = payload.get("active_tasks") or {}
    assert thread_alice in active
    assert thread_bob not in active

    # Cleanup.
    await main.cancellation_manager.cancel(thread_alice, "test cleanup")
    await main.cancellation_manager.cancel(thread_bob, "test cleanup")
