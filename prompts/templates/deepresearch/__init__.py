"""
Deepresearch templates alias.

For compatibility, this module re-exports the deepsearch prompt set so
both names can be used interchangeably without duplicating files.
"""

from prompts.templates.deepsearch import (
    final_summary_prompt,
    formulate_query_prompt,
    get_behavior_prompt,
    related_url_prompt,
    summary_crawl_prompt,
    summary_text_prompt,
)

__all__ = [
    "final_summary_prompt",
    "formulate_query_prompt",
    "get_behavior_prompt",
    "related_url_prompt",
    "summary_crawl_prompt",
    "summary_text_prompt",
]
