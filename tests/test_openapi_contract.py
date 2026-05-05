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


def test_openapi_has_key_paths_and_distinct_resume_schemas():
    spec = app.openapi()
    assert isinstance(spec, dict)

    paths = spec.get("paths", {}) or {}
    assert "/api/interrupt/resume" in paths
    assert "/api/sessions/{thread_id}/resume" in paths
    assert "/api/agents" in paths
    assert "/api/sessions" in paths
    assert "/api/runs/{thread_id}" in paths
    assert "/api/sessions/{thread_id}/continue-research" in paths
    assert "/api/chat/sse" in paths
    assert "/api/sessions/{thread_id}/versions" in paths
    assert "/api/sessions/{thread_id}/evidence" in paths
    assert "/api/chat/sse" in paths
    assert "/api/runs/{thread_id}" in paths

    schemas = (spec.get("components", {}) or {}).get("schemas", {}) or {}
    assert "GraphInterruptResumeRequest" in schemas
    assert "SessionResumeRequest" in schemas

    agents_get = (paths.get("/api/agents", {}) or {}).get("get", {}) or {}
    agents_schema = (
        (agents_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert agents_schema, "/api/agents 200 schema should not be empty (response_model missing?)"
    agents_resolved = _resolve_schema_ref(spec, agents_schema)
    agents_props = agents_resolved.get("properties", {}) or {}
    assert "agents" in agents_props
    assert agents_props["agents"].get("type") == "array"

    sessions_get = (paths.get("/api/sessions", {}) or {}).get("get", {}) or {}
    sessions_schema = (
        (sessions_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert sessions_schema, "/api/sessions 200 schema should not be empty (response_model missing?)"
    sessions_resolved = _resolve_schema_ref(spec, sessions_schema)
    sessions_props = sessions_resolved.get("properties", {}) or {}
    assert sessions_props.get("count", {}).get("type") == "integer"
    assert sessions_props.get("sessions", {}).get("type") == "array"

    comments_get = (paths.get("/api/sessions/{thread_id}/comments", {}) or {}).get("get", {}) or {}
    comments_schema = (
        (comments_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert (
        comments_schema
    ), "/api/sessions/{thread_id}/comments 200 schema should not be empty (response_model missing?)"
    comments_resolved = _resolve_schema_ref(spec, comments_schema)
    comments_props = comments_resolved.get("properties", {}) or {}
    assert comments_props.get("count", {}).get("type") == "integer"
    assert comments_props.get("comments", {}).get("type") == "array"

    versions_get = (paths.get("/api/sessions/{thread_id}/versions", {}) or {}).get("get", {}) or {}
    versions_schema = (
        (versions_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert (
        versions_schema
    ), "/api/sessions/{thread_id}/versions 200 schema should not be empty (response_model missing?)"
    versions_resolved = _resolve_schema_ref(spec, versions_schema)
    versions_props = versions_resolved.get("properties", {}) or {}
    assert versions_props.get("count", {}).get("type") == "integer"
    assert versions_props.get("versions", {}).get("type") == "array"

    evidence_get = (
        (paths.get("/api/sessions/{thread_id}/evidence", {}) or {}).get("get", {}) or {}
    )
    evidence_schema = (
        (evidence_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert (
        evidence_schema
    ), "/api/sessions/{thread_id}/evidence 200 schema should not be empty (response_model missing?)"
    evidence_resolved = _resolve_schema_ref(spec, evidence_schema)
    evidence_props = evidence_resolved.get("properties", {}) or {}
    assert evidence_props.get("sources", {}).get("type") == "array"
    assert evidence_props.get("claims", {}).get("type") == "array"
    assert evidence_props.get("research_brief", {}).get("type") == "object"
    assert evidence_props.get("quality_gates", {}).get("type") == "array"
    assert evidence_props.get("evidence_items", {}).get("type") == "array"
    assert evidence_props.get("citation_annotations", {}).get("type") == "array"
    assert evidence_props.get("timeline", {}).get("type") == "array"
    assert evidence_props.get("supervisor_decisions", {}).get("type") == "array"
    assert evidence_props.get("worker_runs", {}).get("type") == "array"
    assert evidence_props.get("intermediate_steps", {}).get("type") == "array"
    assert evidence_props.get("continue_requests", {}).get("type") == "array"
    assert evidence_props.get("fetched_pages", {}).get("type") == "array"
    assert evidence_props.get("passages", {}).get("type") == "array"

    passage_item = schemas.get("EvidencePassageItem", {}) or {}
    passage_item_props = passage_item.get("properties", {}) or {}
    assert "heading" in passage_item_props
    assert "heading_path" in passage_item_props
    assert "page_title" in passage_item_props
    assert "retrieved_at" in passage_item_props
    assert "method" in passage_item_props
    assert "quote" in passage_item_props
    assert "snippet_hash" in passage_item_props

    claim_item = schemas.get("EvidenceClaim", {}) or {}
    claim_item_props = claim_item.get("properties", {}) or {}
    assert "evidence_passages" in claim_item_props
    assert claim_item_props.get("evidence_passages", {}).get("type") == "array"

    citation_item = schemas.get("CitationAnnotationResponse", {}) or {}
    citation_item_props = citation_item.get("properties", {}) or {}
    assert "marker" in citation_item_props
    assert "evidence_ids" in citation_item_props

    timeline_item = schemas.get("TimelineEventResponse", {}) or {}
    timeline_item_props = timeline_item.get("properties", {}) or {}
    assert "event_type" in timeline_item_props
    assert "order" in timeline_item_props

    worker_item = schemas.get("WorkerRunResponse", {}) or {}
    worker_item_props = worker_item.get("properties", {}) or {}
    assert "worker_id" in worker_item_props
    assert "provider_breakdown" in worker_item_props

    supervisor_item = schemas.get("SupervisorDecisionResponse", {}) or {}
    supervisor_item_props = supervisor_item.get("properties", {}) or {}
    assert "action" in supervisor_item_props
    assert "quality_snapshot" in supervisor_item_props

    step_item = schemas.get("IntermediateStepResponse", {}) or {}
    step_item_props = step_item.get("properties", {}) or {}
    assert "type" in step_item_props
    assert "order" in step_item_props

    continue_req = schemas.get("ContinueResearchRequest", {}) or {}
    continue_req_props = continue_req.get("properties", {}) or {}
    assert "target_type" in continue_req_props
    assert "instruction" in continue_req_props

    continue_resp = schemas.get("ContinueResearchResponse", {}) or {}
    continue_resp_props = continue_resp.get("properties", {}) or {}
    assert "continue_request" in continue_resp_props
    assert "stream_payload" in continue_resp_props

    runs_get = (paths.get("/api/runs/{thread_id}", {}) or {}).get("get", {}) or {}
    runs_schema = (
        (runs_get.get("responses", {}) or {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        or {}
    )
    assert runs_schema, "/api/runs/{thread_id} 200 schema should not be empty (response_model missing?)"
    runs_resolved = _resolve_schema_ref(spec, runs_schema)
    runs_props = runs_resolved.get("properties", {}) or {}
    assert "evidence_summary" in runs_props
    evidence_summary_schema = runs_props.get("evidence_summary", {}) or {}
    evidence_summary_resolved = _resolve_schema_ref(spec, evidence_summary_schema)
    evidence_summary_props = evidence_summary_resolved.get("properties", {}) or {}
    assert evidence_summary_props.get("sources_count", {}).get("type") == "integer"
    assert evidence_summary_props.get("unsupported_claims_count", {}).get("type") == "integer"
