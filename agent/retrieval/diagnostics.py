from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RetrievalDiagnostic:
    origin: str
    channel: str
    method: str
    status: str
    message: str = ""
    provider: str = ""
    result_count: int = 0
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in ("", [], {}, None)}


def diagnostic(
    *,
    origin: str,
    channel: str,
    method: str,
    status: str,
    message: str = "",
    provider: str = "",
    result_count: int = 0,
    retryable: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return RetrievalDiagnostic(
        origin=origin,
        channel=channel,
        method=method,
        status=status,
        message=message,
        provider=provider,
        result_count=result_count,
        retryable=retryable,
        metadata=metadata or {},
    ).to_dict()
