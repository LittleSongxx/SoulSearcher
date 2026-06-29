"""
Academic Search Providers and Tools for SoulSearcher.

Provides access to academic literature:
- arXiv (preprints, no API key)
- Semantic Scholar (papers with citations)
- PubMed (biomedical literature)
"""

from langchain_core.tools import tool

from tools.search.academic.arxiv_provider import ArxivProvider
from tools.search.academic.pubmed_provider import PubMedProvider
from tools.search.academic.semantic_scholar_provider import SemanticScholarProvider


def _format_results(results, provider_name: str, max_results: int = 5) -> str:
    """Shared formatting for academic search results."""
    if not results:
        return f"No {provider_name} results found."
    lines = []
    for i, r in enumerate(results[:max_results]):
        title = getattr(r, "title", None) or "Unknown"
        link = getattr(r, "link", None) or "N/A"
        authors = ", ".join(getattr(r, "authors", [])[:5]) if getattr(r, "authors", None) else "N/A"
        summary = (getattr(r, "summary", None) or "")[:300]
        citation_count = getattr(r, "citation_count", None)
        extra = f"\n   Citations: {citation_count}" if citation_count else ""
        lines.append(
            f"{i+1}. {title}\n   URL: {link}\n   Authors: {authors}{extra}\n   Summary: {summary}"
        )
    return "\n\n".join(lines)


@tool
def arxiv_search(query: str, max_results: int = 5) -> str:
    """Search ArXiv for academic preprints. Use for physics, CS, math, and related fields."""
    return _format_results(ArxivProvider().search(query, max_results=max_results), "ArXiv", max_results)


@tool
def pubmed_search(query: str, max_results: int = 5) -> str:
    """Search PubMed for biomedical literature. Use for medical, biology, and life science topics."""
    return _format_results(PubMedProvider().search(query, max_results=max_results), "PubMed", max_results)


@tool
def semantic_scholar_search(query: str, max_results: int = 5) -> str:
    """Search Semantic Scholar for academic papers with citation data."""
    return _format_results(SemanticScholarProvider().search(query, max_results=max_results), "Semantic Scholar", max_results)


__all__ = [
    "ArxivProvider",
    "PubMedProvider",
    "SemanticScholarProvider",
    "arxiv_search",
    "pubmed_search",
    "semantic_scholar_search",
]
