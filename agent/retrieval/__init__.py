"""Unified retrieval layer for SoulSearcher Deep Research."""

from agent.retrieval.policy import (
    RetrievalPolicy,
    build_retrieval_policy,
    reject_legacy_source_routing,
)


def retrieve_sources(*args, **kwargs):
    from agent.retrieval.gateway import retrieve_sources as _retrieve_sources

    return _retrieve_sources(*args, **kwargs)


def read_source(*args, **kwargs):
    from agent.retrieval.gateway import read_source as _read_source

    return _read_source(*args, **kwargs)


def build_retrieval_tools(*args, **kwargs):
    from agent.retrieval.gateway import build_retrieval_tools as _build_retrieval_tools

    return _build_retrieval_tools(*args, **kwargs)

__all__ = [
    "RetrievalPolicy",
    "build_retrieval_policy",
    "reject_legacy_source_routing",
    "build_retrieval_tools",
    "retrieve_sources",
    "read_source",
]
