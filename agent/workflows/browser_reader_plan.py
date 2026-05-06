from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class ReaderAction:
    action: str
    reason: str
    target: str = ""
    worker_id: str = ""
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value not in (None, "", [], {})
        }


@dataclass
class BrowserReaderPlan:
    enabled: bool
    mode: str
    actions: list[ReaderAction] = field(default_factory=list)
    failure_count: int = 0
    dynamic_page_candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "schema_version": 1,
                "enabled": self.enabled,
                "mode": self.mode,
                "action_count": len(self.actions),
                "actions": [action.to_dict() for action in self.actions],
                "failure_count": self.failure_count,
                "dynamic_page_candidates": self.dynamic_page_candidates,
            }.items()
            if value not in (None, "", [], {})
        }


def build_browser_reader_plan(
    *,
    worker_runs: list[dict[str, Any]] | None = None,
    fetched_pages: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _configurable(config)
    enabled = _truthy(cfg.get("deepsearch_reader_plan_enabled", True))
    mode = str(cfg.get("deepsearch_reader_plan_mode") or "search_fetch_browser_hint").strip()
    actions: list[ReaderAction] = []
    failures = []
    dynamic_candidates: list[str] = []

    for page in fetched_pages or []:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or "").strip()
        error = str(page.get("error") or page.get("status") or "").lower()
        content = str(page.get("content") or page.get("text") or "")
        if error and any(marker in error for marker in ("error", "failed", "timeout", "forbidden", "consumed")):
            failures.append(url or error)
            actions.append(
                ReaderAction(
                    action="retry_with_browser_or_reader",
                    reason=f"fetch failure: {error[:120]}",
                    target=url,
                    priority=80,
                )
            )
        elif url and _looks_dynamic_or_file(url, content):
            dynamic_candidates.append(url)
            actions.append(
                ReaderAction(
                    action="open_with_browser_or_specialized_reader",
                    reason="source likely needs browser, PDF, table, or dynamic-page reader",
                    target=url,
                    priority=60,
                )
            )

    for run in worker_runs or []:
        if not isinstance(run, dict):
            continue
        errors = run.get("errors") or []
        if errors:
            actions.append(
                ReaderAction(
                    action="worker_followup_read",
                    reason="worker completed with search/read errors",
                    target=str(run.get("focus") or run.get("topic") or ""),
                    worker_id=str(run.get("worker_id") or ""),
                    priority=70,
                )
            )

    if not actions:
        for source in (sources or [])[:5]:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or source.get("source_url") or "").strip()
            if url and _looks_dynamic_or_file(url, ""):
                dynamic_candidates.append(url)
                actions.append(
                    ReaderAction(
                        action="inspect_high_value_source",
                        reason="selected source may benefit from richer reader tooling",
                        target=url,
                        priority=40,
                    )
                )

    actions.sort(key=lambda action: action.priority, reverse=True)
    return BrowserReaderPlan(
        enabled=enabled,
        mode=mode,
        actions=actions[:12] if enabled else [],
        failure_count=len(failures),
        dynamic_page_candidates=list(dict.fromkeys(dynamic_candidates))[:12],
    ).to_dict()


def _looks_dynamic_or_file(url: str, content: str) -> bool:
    lower = url.lower()
    if any(lower.endswith(ext) or ext in lower for ext in (".pdf", ".xlsx", ".csv", ".ppt", ".doc")):
        return True
    domain = urlparse(url).netloc.lower()
    if any(marker in domain for marker in ("docs.google", "notion.site", "airtable", "tableau", "powerbi")):
        return True
    snippet = content.lower()[:500]
    return any(marker in snippet for marker in ("enable javascript", "__next_data__", "window.__", "loading..."))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _configurable(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    cfg = config.get("configurable")
    if isinstance(cfg, dict):
        merged = dict(config)
        merged.update(cfg)
        return merged
    return config
