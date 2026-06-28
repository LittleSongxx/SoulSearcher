from __future__ import annotations

from typing import Any, TypedDict


class StreamEvent(TypedDict):
    type: str
    data: Any


class SseEvent(TypedDict, total=False):
    id: int
    event: str
    data: Any


class RetrievalPolicy(TypedDict, total=False):
    schema_version: int
    allowed_origins: list[str]
    channels: list[str]
    methods: list[str]
    profiles: list[str]
    domain_policy: dict[str, Any]
    corpus_policy: dict[str, Any]
    connector_policy: dict[str, Any]
    budget: dict[str, Any]
