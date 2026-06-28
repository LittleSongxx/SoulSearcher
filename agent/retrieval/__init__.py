"""Unified retrieval layer for Weaver Deep Research."""

from agent.retrieval.gateway import (
    build_retrieval_tools,
    read_source,
    retrieve_sources,
)
from agent.retrieval.policy import (
    RetrievalPolicy,
    build_retrieval_policy,
    reject_legacy_source_routing,
)

__all__ = [
    "RetrievalPolicy",
    "build_retrieval_policy",
    "reject_legacy_source_routing",
    "build_retrieval_tools",
    "retrieve_sources",
    "read_source",
]
