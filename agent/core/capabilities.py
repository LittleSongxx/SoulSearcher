from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    tier: str
    description: str
    setting_name: str | None = None


CORE_CAPABILITIES: tuple[Capability, ...] = (
    Capability("research_graph", "core", "DeepResearch LangGraph execution pipeline."),
    Capability("a2a", "core", "A2A 1.0 JSON-RPC interface for delegated research."),
    Capability("sse", "core", "Streaming research protocol exposed over SSE."),
    Capability("runs", "core", "Durable run, event, evidence, and artifact tracking."),
    Capability("report", "core", "Evidence-led report generation and quality checks."),
)


OPTIONAL_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "sandbox",
        "optional",
        "Browser, shell, file, presentation, and sheet sandbox tools.",
        "sandbox_mode",
    ),
    Capability(
        "channels",
        "optional",
        "External channel adapters such as Feishu.",
        "channels_enabled",
    ),
    Capability(
        "skills_marketplace",
        "optional",
        "Public/custom skill installation and evolution surfaces.",
    ),
    Capability(
        "sdk_generated_artifacts",
        "optional",
        "Generated SDK distribution files.",
    ),
    Capability(
        "export_templates",
        "optional",
        "Report export templates and conversion helpers.",
    ),
)


def _optional_capability_enabled(item: Capability, settings: Any | None) -> bool:
    if item.name == "sandbox":
        mode = (getattr(settings, "sandbox_mode", "") if settings is not None else "").strip()
        return bool(mode and mode.lower() != "none")
    if item.setting_name and settings is not None:
        return bool(getattr(settings, item.setting_name, False))
    return True


def capability_payload(settings: Any | None = None) -> dict[str, list[dict[str, Any]]]:
    def dump_core(items: tuple[Capability, ...]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "tier": item.tier,
                "description": item.description,
                "enabled": True,
            }
            for item in items
        ]

    def dump_optional(items: tuple[Capability, ...]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "tier": item.tier,
                "description": item.description,
                "enabled": _optional_capability_enabled(item, settings),
            }
            for item in items
        ]

    return {
        "core": dump_core(CORE_CAPABILITIES),
        "optional": dump_optional(OPTIONAL_CAPABILITIES),
    }


def register_capability_metadata(app: Any, settings: Any | None = None) -> None:
    """Attach core/optional capability metadata to the application state."""

    app.state.capabilities = capability_payload(settings)
