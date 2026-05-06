from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from common.config import settings


@dataclass(frozen=True)
class LoopGuardPolicy:
    max_repeated_query: int = 1
    max_repeated_url: int = 2
    max_repeated_tool_call: int = 1
    max_empty_result_streak: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LoopGuardDecision:
    allowed: bool
    reason: str = ""
    key: str = ""
    kind: str = ""
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


class ResearchLoopGuard:
    def __init__(self, policy: LoopGuardPolicy) -> None:
        self.policy = policy
        self.query_counts: dict[str, int] = {}
        self.url_counts: dict[str, int] = {}
        self.tool_call_counts: dict[str, int] = {}
        self.empty_result_streak = 0
        self.decisions: list[LoopGuardDecision] = []
        self._lock = Lock()

    def before_search_query(self, query: str) -> LoopGuardDecision:
        with self._lock:
            key = normalize_query(query)
            count = self.query_counts.get(key, 0)
            if (
                key
                and self.policy.max_repeated_query > 0
                and count >= self.policy.max_repeated_query
            ):
                return self._deny("query", key, count + 1, "repeated search query")
            self.query_counts[key] = count + 1
            decision = LoopGuardDecision(
                allowed=True, key=key, kind="query", count=count + 1
            )
            self.decisions.append(decision)
            return decision

    def before_tool_call(self, tool_name: str, args: Any) -> LoopGuardDecision:
        with self._lock:
            key = tool_call_key(tool_name, args)
            count = self.tool_call_counts.get(key, 0)
            if (
                key
                and self.policy.max_repeated_tool_call > 0
                and count >= self.policy.max_repeated_tool_call
            ):
                return self._deny("tool_call", key, count + 1, "repeated tool call")
            self.tool_call_counts[key] = count + 1
            decision = LoopGuardDecision(
                allowed=True, key=key, kind="tool_call", count=count + 1
            )
            self.decisions.append(decision)
            return decision

    def record_search_results(
        self, query: str, results: list[dict[str, Any]]
    ) -> LoopGuardDecision:
        with self._lock:
            if not results:
                self.empty_result_streak += 1
                if (
                    self.policy.max_empty_result_streak > 0
                    and self.empty_result_streak >= self.policy.max_empty_result_streak
                ):
                    return self._deny(
                        "empty_results",
                        normalize_query(query),
                        self.empty_result_streak,
                        "empty result streak",
                    )
                decision = LoopGuardDecision(
                    allowed=True,
                    kind="empty_results",
                    key=normalize_query(query),
                    count=self.empty_result_streak,
                )
                self.decisions.append(decision)
                return decision
            self.empty_result_streak = 0
            repeated_urls = []
            for result in results or []:
                if not isinstance(result, dict):
                    continue
                url = normalize_url(str(result.get("url") or result.get("href") or ""))
                if not url:
                    continue
                count = self.url_counts.get(url, 0) + 1
                self.url_counts[url] = count
                if (
                    self.policy.max_repeated_url > 0
                    and count > self.policy.max_repeated_url
                ):
                    repeated_urls.append(url)
            if repeated_urls:
                return self._deny(
                    "url",
                    repeated_urls[0],
                    self.url_counts.get(repeated_urls[0], 0),
                    "repeated source url",
                )
            decision = LoopGuardDecision(
                allowed=True,
                kind="results",
                key=normalize_query(query),
                count=len(results or []),
            )
            self.decisions.append(decision)
            return decision

    def to_artifact(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "policy": self.policy.to_dict(),
                "query_count": len(self.query_counts),
                "url_count": len(self.url_counts),
                "tool_call_count": len(self.tool_call_counts),
                "empty_result_streak": self.empty_result_streak,
                "denied_count": len(
                    [decision for decision in self.decisions if not decision.allowed]
                ),
                "decisions": [
                    decision.to_dict()
                    for decision in self.decisions
                    if not decision.allowed
                ],
            }

    def _deny(self, kind: str, key: str, count: int, reason: str) -> LoopGuardDecision:
        decision = LoopGuardDecision(
            allowed=False, reason=reason, key=key, kind=kind, count=count
        )
        self.decisions.append(decision)
        return decision


def build_loop_guard_policy(config: dict[str, Any]) -> LoopGuardPolicy:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    return LoopGuardPolicy(
        max_repeated_query=_int_config(
            cfg,
            "deepsearch_loop_max_repeated_query",
            int(getattr(settings, "deepsearch_loop_max_repeated_query", 1) or 1),
        ),
        max_repeated_url=_int_config(
            cfg,
            "deepsearch_loop_max_repeated_url",
            int(getattr(settings, "deepsearch_loop_max_repeated_url", 2) or 2),
        ),
        max_repeated_tool_call=_int_config(
            cfg,
            "deepsearch_loop_max_repeated_tool_call",
            int(getattr(settings, "deepsearch_loop_max_repeated_tool_call", 1) or 1),
        ),
        max_empty_result_streak=_int_config(
            cfg,
            "deepsearch_loop_max_empty_result_streak",
            int(getattr(settings, "deepsearch_loop_max_empty_result_streak", 3) or 3),
        ),
    )


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def normalize_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value.lower()
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=False)
            if not key.lower().startswith("utm_")
        )
    )
    path = parsed.path.rstrip("/") or parsed.path
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def tool_call_key(tool_name: str, args: Any) -> str:
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        payload = str(args)
    raw = f"{tool_name or ''}|{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _int_config(cfg: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return int(default)
