import json


def test_build_openapi_spec_contains_openapi_version_and_research_path():
    from scripts.export_openapi import build_openapi_spec

    spec = build_openapi_spec()
    assert isinstance(spec, dict)
    assert "openapi" in spec
    paths = spec.get("paths") or {}
    assert "/api/research/sse" in paths
    assert "/api/chat" not in paths
    json.dumps(spec)
