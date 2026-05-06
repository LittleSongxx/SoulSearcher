from main import app


def _resolve_schema_ref(spec: dict, schema: dict) -> dict:
    ref = (schema or {}).get("$ref")
    if not ref or not isinstance(ref, str):
        return schema or {}

    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        return schema or {}

    name = ref[len(prefix) :]
    components = spec.get("components", {}) or {}
    schemas = components.get("schemas", {}) or {}
    return schemas.get(name, {}) or {}


def _response_schema(paths: dict, path: str, method: str = "get") -> dict:
    return ((paths.get(path, {}) or {}).get(method, {}) or {}).get("responses", {}).get(
        "200", {}
    ).get("content", {}).get("application/json", {}).get("schema", {}) or {}


def test_openapi_exposes_research_workspace_core_paths_only():
    spec = app.openapi()
    assert isinstance(spec, dict)

    paths = spec.get("paths", {}) or {}
    expected_core_paths = {
        "/api/research/sse",
        "/api/research/cancel/{thread_id}",
        "/api/research/cancel-all",
        "/api/interrupt/resume",
        "/api/sessions",
        "/api/sessions/{thread_id}",
        "/api/sessions/{thread_id}/state",
        "/api/sessions/{thread_id}/evidence",
        "/api/sessions/{thread_id}/continue-research",
        "/api/runs/{thread_id}",
        "/api/export/{thread_id}",
        "/api/export/templates",
        "/api/documents/upload",
        "/api/documents/list",
        "/api/documents/search",
    }
    for path in expected_core_paths:
        assert path in paths

    removed_product_paths = {
        "/api/chat",
        "/api/chat/sse",
        "/api/chat/cancel/{thread_id}",
        "/api/chat/cancel-all",
        "/api/research",
        "/api/agents",
        "/api/agents/{agent_id}",
        "/api/skills",
        "/api/skills/{skill_id}",
        "/api/asr/recognize",
        "/api/asr/upload",
        "/api/asr/status",
        "/api/tts/synthesize",
        "/api/tts/voices",
        "/api/tts/status",
        "/api/browser/{thread_id}/info",
        "/api/browser/{thread_id}/screenshot",
        "/api/events/{thread_id}",
        "/api/screenshots",
        "/api/screenshots/{filename}",
        "/api/triggers",
        "/api/triggers/webhook",
        "/api/webhook/{trigger_id}",
        "/api/mcp/config",
        "/api/sandbox/browser/diagnose",
    }
    for path in removed_product_paths:
        assert path not in paths


def test_openapi_research_workspace_response_schemas_are_typed():
    spec = app.openapi()
    paths = spec.get("paths", {}) or {}
    schemas = (spec.get("components", {}) or {}).get("schemas", {}) or {}

    assert "ResearchRequest" in schemas
    assert "GraphInterruptResumeRequest" in schemas
    assert "SessionResumeRequest" in schemas
    assert "ChatRequest" not in schemas
    assert "AgentUpsertPayload" not in schemas
    assert "SupportChatRequest" not in schemas

    research_req_props = schemas.get("ResearchRequest", {}).get("properties", {}) or {}
    assert "query" in research_req_props
    assert "search_mode" in research_req_props
    assert "deepsearch_config" in research_req_props
    assert "agent_id" not in research_req_props
    assert "skill_id" not in research_req_props

    sessions_schema = _response_schema(paths, "/api/sessions")
    assert sessions_schema, "/api/sessions 200 schema should not be empty"
    sessions_resolved = _resolve_schema_ref(spec, sessions_schema)
    sessions_props = sessions_resolved.get("properties", {}) or {}
    assert sessions_props.get("count", {}).get("type") == "integer"
    assert sessions_props.get("sessions", {}).get("type") == "array"

    evidence_schema = _response_schema(paths, "/api/sessions/{thread_id}/evidence")
    assert (
        evidence_schema
    ), "/api/sessions/{thread_id}/evidence 200 schema should not be empty"
    evidence_resolved = _resolve_schema_ref(spec, evidence_schema)
    evidence_props = evidence_resolved.get("properties", {}) or {}
    for field in (
        "sources",
        "claims",
        "quality_gates",
        "evidence_items",
        "citation_annotations",
        "timeline",
        "supervisor_decisions",
        "worker_runs",
        "intermediate_steps",
        "continue_requests",
        "fetched_pages",
        "passages",
    ):
        assert evidence_props.get(field, {}).get("type") == "array"
    assert evidence_props.get("research_brief", {}).get("type") == "object"

    passage_item_props = (
        schemas.get("EvidencePassageItem", {}).get("properties", {}) or {}
    )
    for field in (
        "heading",
        "heading_path",
        "page_title",
        "retrieved_at",
        "method",
        "quote",
        "snippet_hash",
    ):
        assert field in passage_item_props

    claim_item_props = schemas.get("EvidenceClaim", {}).get("properties", {}) or {}
    assert claim_item_props.get("evidence_passages", {}).get("type") == "array"

    continue_req_props = (
        schemas.get("ContinueResearchRequest", {}).get("properties", {}) or {}
    )
    assert "target_type" in continue_req_props
    assert "instruction" in continue_req_props

    continue_resp_props = (
        schemas.get("ContinueResearchResponse", {}).get("properties", {}) or {}
    )
    assert "continue_request" in continue_resp_props
    assert "stream_payload" in continue_resp_props

    runs_schema = _response_schema(paths, "/api/runs/{thread_id}")
    assert runs_schema, "/api/runs/{thread_id} 200 schema should not be empty"
    runs_resolved = _resolve_schema_ref(spec, runs_schema)
    runs_props = runs_resolved.get("properties", {}) or {}
    evidence_summary_resolved = _resolve_schema_ref(
        spec, runs_props.get("evidence_summary", {}) or {}
    )
    evidence_summary_props = evidence_summary_resolved.get("properties", {}) or {}
    assert evidence_summary_props.get("sources_count", {}).get("type") == "integer"
    assert (
        evidence_summary_props.get("unsupported_claims_count", {}).get("type")
        == "integer"
    )
