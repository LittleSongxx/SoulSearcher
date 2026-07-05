from __future__ import annotations

from fastapi.testclient import TestClient


def test_main_entrypoint_exposes_fully_wired_app() -> None:
    import main

    client = TestClient(main.app)
    paths = {getattr(route, "path", "") for route in main.app.routes}
    openapi_paths = set(main.app.openapi()["paths"])
    optional = {
        item["name"]: item
        for item in main.app.state.capabilities.get("optional", [])
    }

    assert main.create_app() is main.app
    assert hasattr(main.app.state, "runtime_context")
    assert "core" in main.app.state.capabilities
    assert optional["channels"]["enabled"] is False
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in {200, 503}
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/health/agent").status_code == 200
    assert "/api/a2a" in paths
    assert "/api/research/sse" in openapi_paths
    assert "/api/runs/background" in openapi_paths
    assert client.get("/api/runs").status_code == 200
    assert client.post("/api/runs/background", json={"query": "ping"}).status_code in {
        200,
        403,
        409,
    }
    assert "/api/channels" not in openapi_paths
    assert hasattr(main.app.state.runtime_context.research_execution_service, "stream")


def test_app_module_factory_preserves_core_routes() -> None:
    from agent.api import app as app_module

    app = app_module.create_app()
    client = TestClient(app)
    paths = {getattr(route, "path", "") for route in app.routes}
    openapi_paths = set(app.openapi()["paths"])
    optional = {item["name"]: item for item in app.state.capabilities["optional"]}

    assert app is app_module.app
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in {200, 503}
    assert client.get("/api/health/agent").status_code == 200
    assert "/api/a2a" in paths
    assert "/api/research/sse" in openapi_paths
    assert "/api/runs/background" in openapi_paths
    assert client.get("/api/runs").status_code == 200
    assert "/api/channels" not in openapi_paths
    assert optional["channels"]["enabled"] is False
    assert app.state.runtime_context.research_graph is app_module.research_graph
    assert (
        app.state.runtime_context.research_execution_service
        is app_module.research_execution_service
    )
