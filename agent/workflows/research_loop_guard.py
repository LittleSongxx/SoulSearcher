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
    warn_threshold: int = 3
    hard_limit: int = 5
    window_size: int = 20
    tool_freq_warn: int = 30
    tool_freq_hard_limit: int = 50

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LoopGuardDecision:
    allowed: bool
    reason: str = ""
    key: str = ""
    kind: str = ""
    count: int = 0
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str = ""
    code: str = ""
    tool_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


class ToolCallGuardrail:
    def __init__(
        self,
        denied_tools: list[str] | None = None,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.denied_tools = [t.strip().lower() for t in (denied_tools or []) if t.strip()]
        self.allowed_domains = [d.strip().lower() for d in (allowed_domains or []) if d.strip()]
        self.decisions: list[GuardrailDecision] = []

    def evaluate(self, tool_name: str, args: Any = None) -> GuardrailDecision:
        name_lower = (tool_name or "").strip().lower()
        if name_lower and name_lower in self.denied_tools:
            decision = GuardrailDecision(
                allowed=False,
                reason=f"tool '{tool_name}' is denied by guardrail policy",
                code="guardrail.denied_tool",
                tool_name=tool_name,
            )
            self.decisions.append(decision)
            return decision
        if self.allowed_domains and isinstance(args, dict):
            url = str(args.get("url") or args.get("href") or "")
            if url:
                try:
                    domain = urlsplit(url).netloc.lower()
                except ValueError:
                    domain = ""
                if domain and not any(domain.endswith(d) for d in self.allowed_domains):
                    decision = GuardrailDecision(
                        allowed=False,
                        reason=f"domain '{domain}' not in allowed domains",
                        code="guardrail.denied_domain",
                        tool_name=tool_name,
                    )
                    self.decisions.append(decision)
                    return decision
        decision = GuardrailDecision(allowed=True, tool_name=tool_name)
        self.decisions.append(decision)
        return decision

    def to_artifact(self) -> dict[str, Any]:
        denied = [d.to_dict() for d in self.decisions if not d.allowed]
        return {
            "denied_tools": self.denied_tools,
            "allowed_domains": self.allowed_domains,
            "total_evaluations": len(self.decisions),
            "denied_count": len(denied),
            "denied_decisions": denied,
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
        self._window: list[str] = []
        self._window_warned: set[str] = set()
        self._tool_freq: dict[str, int] = {}
        self._tool_freq_warned: set[str] = set()

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

            window_decision = self._check_sliding_window(tool_name, args)
            if window_decision is not None:
                return window_decision

            freq_decision = self._check_tool_frequency(tool_name)
            if freq_decision is not None:
                return freq_decision

            decision = LoopGuardDecision(
                allowed=True, key=key, kind="tool_call", count=count + 1
            )
            self.decisions.append(decision)
            return decision

    def _check_sliding_window(self, tool_name: str, args: Any) -> LoopGuardDecision | None:
        call_hash = tool_call_key(tool_name, args)
        self._window.append(call_hash)
        if len(self._window) > self.policy.window_size:
            self._window[:] = self._window[-self.policy.window_size:]
        count = self._window.count(call_hash)
        if self.policy.hard_limit > 0 and count >= self.policy.hard_limit:
            return self._deny(
                "window_hard_limit", call_hash, count,
                f"tool call repeated {count} times in sliding window — forced stop",
            )
        if self.policy.warn_threshold > 0 and count >= self.policy.warn_threshold:
            if call_hash not in self._window_warned:
                self._window_warned.add(call_hash)
                decision = LoopGuardDecision(
                    allowed=True, key=call_hash, kind="window_warn",
                    count=count,
                    warning=f"tool call repeated {count} times in sliding window — consider a different approach",
                )
                self.decisions.append(decision)
                return decision
        return None

    def _check_tool_frequency(self, tool_name: str) -> LoopGuardDecision | None:
        name = (tool_name or "").strip()
        if not name:
            return None
        freq = self._tool_freq.get(name, 0) + 1
        self._tool_freq[name] = freq
        if self.policy.tool_freq_hard_limit > 0 and freq >= self.policy.tool_freq_hard_limit:
            return self._deny(
                "tool_freq_hard_limit", name, freq,
                f"tool '{name}' called {freq} times — exceeded per-tool safety limit",
            )
        if self.policy.tool_freq_warn > 0 and freq >= self.policy.tool_freq_warn:
            if name not in self._tool_freq_warned:
                self._tool_freq_warned.add(name)
                decision = LoopGuardDecision(
                    allowed=True, key=name, kind="tool_freq_warn",
                    count=freq,
                    warning=f"tool '{name}' called {freq} times — consider wrapping up",
                )
                self.decisions.append(decision)
                return decision
        return None

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
            denied = [d for d in self.decisions if not d.allowed]
            warned = [d for d in self.decisions if d.allowed and d.warning]
            return {
                "schema_version": 2,
                "policy": self.policy.to_dict(),
                "query_count": len(self.query_counts),
                "url_count": len(self.url_counts),
                "tool_call_count": len(self.tool_call_counts),
                "empty_result_streak": self.empty_result_streak,
                "denied_count": len(denied),
                "warned_count": len(warned),
                "window_size": len(self._window),
                "tool_freq": dict(self._tool_freq),
                "decisions": [d.to_dict() for d in denied],
                "warnings": [d.to_dict() for d in warned],
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
        warn_threshold=_int_config(
            cfg,
            "deepsearch_loop_warn_threshold",
            int(getattr(settings, "deepsearch_loop_warn_threshold", 3) or 3),
        ),
        hard_limit=_int_config(
            cfg,
            "deepsearch_loop_hard_limit",
            int(getattr(settings, "deepsearch_loop_hard_limit", 5) or 5),
        ),
        window_size=_int_config(
            cfg,
            "deepsearch_loop_window_size",
            int(getattr(settings, "deepsearch_loop_window_size", 20) or 20),
        ),
        tool_freq_warn=_int_config(
            cfg,
            "deepsearch_loop_tool_freq_warn",
            int(getattr(settings, "deepsearch_loop_tool_freq_warn", 30) or 30),
        ),
        tool_freq_hard_limit=_int_config(
            cfg,
            "deepsearch_loop_tool_freq_hard_limit",
            int(getattr(settings, "deepsearch_loop_tool_freq_hard_limit", 50) or 50),
        ),
    )


def build_tool_call_guardrail(config: dict[str, Any]) -> ToolCallGuardrail:
    cfg = config.get("configurable") if isinstance(config, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    denied_raw = str(cfg.get("deepsearch_guardrail_denied_tools") or getattr(settings, "deepsearch_guardrail_denied_tools", "") or "")
    domains_raw = str(cfg.get("deepsearch_guardrail_allowed_domains") or getattr(settings, "deepsearch_guardrail_allowed_domains", "") or "")
    denied_tools = [t.strip() for t in denied_raw.split(",") if t.strip()] if denied_raw else []
    allowed_domains = [d.strip() for d in domains_raw.split(",") if d.strip()] if domains_raw else []
    return ToolCallGuardrail(denied_tools=denied_tools, allowed_domains=allowed_domains)


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
