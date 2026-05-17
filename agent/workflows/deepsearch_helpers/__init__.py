"""
Deepsearch sub-package.

Provides helper modules extracted from the monolithic ``deepsearch_optimized.py``
to improve readability and maintainability.  The top-level entry points
(``run_deepsearch_*``) remain in ``deepsearch_optimized.py`` and import from
these modules.
"""

from agent.workflows.deepsearch_helpers.config_utils import (
    _auto_mode_prefers_linear,
    _browser_visualization_enabled,
    _check_cancel,
    _configurable_bool,
    _configurable_float,
    _configurable_int,
    _configurable_value,
    _normalize_deepsearch_mode,
    _normalize_multi_search_results,
    _resolve_deepsearch_mode,
    _resolve_provider_profile,
    _resolve_search_strategy,
)
from agent.workflows.deepsearch_helpers.token_estimation import (
    _budget_stop_reason,
    _estimate_tokens_from_results,
    _estimate_tokens_from_text,
    _should_skip_expensive_postprocessing,
)
from agent.workflows.deepsearch_helpers.text_utils import (
    _canonical_text_url,
    _inline_text,
    _is_low_value_evidence_text,
    _is_short_claim_sentence,
    _item_snippet,
    _split_claim_sentences,
)
from agent.workflows.deepsearch_helpers.events import (
    _build_feature_trace,
    _compact_search_results,
    _emit_event,
    _event_results_limit,
    _provider_breakdown,
    _resolve_event_emitter,
    _safe_filename,
    _save_deepsearch_data,
)

__all__ = [
    "_auto_mode_prefers_linear",
    "_browser_visualization_enabled",
    "_budget_stop_reason",
    "_build_feature_trace",
    "_canonical_text_url",
    "_check_cancel",
    "_compact_search_results",
    "_configurable_bool",
    "_configurable_float",
    "_configurable_int",
    "_configurable_value",
    "_emit_event",
    "_estimate_tokens_from_results",
    "_estimate_tokens_from_text",
    "_event_results_limit",
    "_inline_text",
    "_is_low_value_evidence_text",
    "_is_short_claim_sentence",
    "_item_snippet",
    "_normalize_deepsearch_mode",
    "_normalize_multi_search_results",
    "_provider_breakdown",
    "_resolve_deepsearch_mode",
    "_resolve_event_emitter",
    "_resolve_provider_profile",
    "_resolve_search_strategy",
    "_safe_filename",
    "_save_deepsearch_data",
    "_should_skip_expensive_postprocessing",
    "_split_claim_sentences",
]
