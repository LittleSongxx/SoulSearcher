import json

from common.chat_stream_translate import translate_legacy_line_to_sse
from common.research_events import build_research_run_event, canonical_research_event_type


def test_research_run_event_maps_legacy_stream_types():
    event = build_research_run_event("status", {"step": "planning", "text": "Planning"}, seq=7)

    assert event["type"] == "plan.delta"
    assert event["legacy_type"] == "status"
    assert event["sequence"] == 7
    assert event["schema_version"] == 1


def test_translate_legacy_line_includes_research_event_envelope():
    sse = translate_legacy_line_to_sse(
        '0:{"type":"completion","data":{"content":"done"}}\n',
        seq=3,
    )

    data_line = next(line for line in sse.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line[len("data: ") :])

    assert payload["type"] == "completion"
    assert payload["research_event"]["type"] == "report.completed"
    assert payload["research_event"]["sequence"] == 3


def test_unknown_legacy_event_is_namespaced():
    assert canonical_research_event_type("quality_gate_evaluated") == "quality.updated"
    assert canonical_research_event_type("custom_event") == "custom.event"
