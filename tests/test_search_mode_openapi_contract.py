from scripts.export_openapi import build_openapi_spec


def _search_mode_schema(spec: dict, schema_name: str) -> dict:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    body = schemas.get(schema_name) or {}
    props = body.get("properties") or {}
    return props.get("search_mode") or {}


def _anyof(schema: dict) -> list[dict]:
    # OpenAPI may represent optional types either as anyOf([...]) or directly.
    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        return schema["anyOf"]
    return [schema]


def test_search_mode_is_contractually_an_object_not_a_string():
    spec = build_openapi_spec()

    for name in ("ResearchRequest", "GraphInterruptResumeRequest"):
        schema = _search_mode_schema(spec, name)
        anyof = _anyof(schema)

        # Contract: search_mode should be the structured SearchMode object (nullable/optional ok),
        # but should NOT advertise string/object passthrough types.
        assert not any(s.get("type") == "string" for s in anyof), (name, schema)
        assert not any(
            s.get("type") == "object" and bool(s.get("additionalProperties"))
            for s in anyof
        ), (name, schema)
