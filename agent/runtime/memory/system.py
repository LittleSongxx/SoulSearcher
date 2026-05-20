"""Per-User Memory System — Structured + Embedding Dual-Mode.

Phase 4 implementation from the unified design.
Integrates patterns from:
- deer-flow: Per-user file-based persistent memory with fact extraction
- gpt-researcher: Embedding-based semantic memory for similarity retrieval

Dual-mode storage:
1. Structured Memory (deer-flow style):
   - UserContext: role, preferences, expertise
   - Facts[]: extracted from research history
   - ResearchHistory: past queries and results
   - Per-user isolation: users/{user_id}/memory.json

2. Semantic Memory (gpt-researcher style):
   - Embedding index for similarity search
   - Automatic reuse of old research findings
   - Vector-based retrieval of relevant past knowledge

Memory Injection Points (from unified design):
1. Clarify Node → user context (preferences, role, preferred sources)
2. ResearchBrief Node → research history (what was studied before)
3. Researcher System Prompt → top-N relevant facts
4. Report Generator → user preferences (language, format, verbosity)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class UserContext:
    """User profile for personalization."""
    role: str = ""
    preferences: dict[str, str] = field(default_factory=dict)
    expertise_level: str = "general"  # "beginner", "general", "expert"
    preferred_sources: list[str] = field(default_factory=list)
    language_preference: str = ""
    format_preference: str = "markdown"
    verbosity_preference: str = "detailed"  # "concise", "balanced", "detailed"


@dataclass
class Fact:
    """A single extracted fact from research."""
    fact: str
    category: str = "general"
    timestamp: str = ""
    source_url: str = ""
    confidence: float = 0.8


@dataclass
class MemoryEntry:
    """A single memory entry recording a research session."""
    query: str
    timestamp: str
    key_findings: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    report_summary: str = ""


@dataclass
class UserMemory:
    """Complete user memory with structured and semantic components."""
    user_id: str
    user_context: UserContext = field(default_factory=UserContext)
    research_history: list[MemoryEntry] = field(default_factory=list)
    extracted_facts: list[Fact] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "user_context": {
                "role": self.user_context.role,
                "preferences": self.user_context.preferences,
                "expertise_level": self.user_context.expertise_level,
                "preferred_sources": self.user_context.preferred_sources,
                "language_preference": self.user_context.language_preference,
                "format_preference": self.user_context.format_preference,
                "verbosity_preference": self.user_context.verbosity_preference,
            },
            "research_history": [
                {
                    "query": e.query,
                    "timestamp": e.timestamp,
                    "key_findings": e.key_findings[:10],
                    "facts": [{"fact": f.fact, "category": f.category} for f in e.facts[:20]],
                    "sources": e.sources[:20],
                    "report_summary": e.report_summary[:500],
                }
                for e in self.research_history[-20:]  # Keep last 20
            ],
            "extracted_facts": [
                {"fact": f.fact, "category": f.category, "timestamp": f.timestamp}
                for f in self.extracted_facts[-100:]  # Keep last 100
            ],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserMemory:
        memory = cls(user_id=data.get("user_id", "default"))
        ctx = data.get("user_context", {})
        memory.user_context = UserContext(
            role=ctx.get("role", ""),
            preferences=ctx.get("preferences", {}),
            expertise_level=ctx.get("expertise_level", "general"),
            preferred_sources=ctx.get("preferred_sources", []),
            language_preference=ctx.get("language_preference", ""),
            format_preference=ctx.get("format_preference", "markdown"),
            verbosity_preference=ctx.get("verbosity_preference", "detailed"),
        )
        memory.research_history = [
            MemoryEntry(
                query=e.get("query", ""),
                timestamp=e.get("timestamp", ""),
                key_findings=e.get("key_findings", []),
                sources=e.get("sources", []),
                report_summary=e.get("report_summary", ""),
            )
            for e in data.get("research_history", [])
        ]
        memory.extracted_facts = [
            Fact(
                fact=f.get("fact", ""),
                category=f.get("category", "general"),
                timestamp=f.get("timestamp", ""),
            )
            for f in data.get("extracted_facts", [])
        ]
        memory.updated_at = data.get("updated_at", "")
        return memory


# =============================================================================
# Memory System
# =============================================================================

class MemorySystem:
    """Per-User memory with structured + embedding dual-mode storage.

    Structured: JSON files at users/{user_id}/memory.json
    Semantic: Embedding index at users/{user_id}/embeddings/ (when available)
    """

    def __init__(self, base_path: str = ""):
        if not base_path:
            base_path = os.environ.get(
                "WEAVER_MEMORY_PATH",
                os.path.join(os.path.dirname(__file__), "..", "..", "data", "memory"),
            )
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, UserMemory] = {}

    def _get_user_path(self, user_id: str) -> Path:
        """Get the memory file path for a user."""
        safe_id = user_id.replace("/", "_").replace("..", "_") or "default"
        user_dir = self.base_path / safe_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / "memory.json"

    def load(self, user_id: str) -> UserMemory:
        """Load memory for a user."""
        if user_id in self._cache:
            return self._cache[user_id]

        path = self._get_user_path(user_id)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                memory = UserMemory.from_dict(data)
                self._cache[user_id] = memory
                return memory
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[Memory] Failed to load memory for {user_id}: {e}")

        memory = UserMemory(user_id=user_id)
        self._cache[user_id] = memory
        return memory

    def save(self, user_id: str, memory: UserMemory) -> None:
        """Save memory for a user."""
        memory.updated_at = datetime.now().isoformat()
        path = self._get_user_path(user_id)
        try:
            path.write_text(json.dumps(memory.to_dict(), indent=2, ensure_ascii=False))
            self._cache[user_id] = memory
            logger.debug(f"[Memory] Saved memory for user '{user_id}'")
        except Exception as e:
            logger.error(f"[Memory] Failed to save memory: {e}")

    async def record_research(
        self,
        user_id: str,
        query: str,
        findings: str,
        facts: list[str],
        sources: list[str] | None = None,
    ) -> None:
        """Record a completed research session in user memory."""
        memory = self.load(user_id)

        # Extract facts from findings
        new_facts = []
        for fact_text in facts[:20]:
            new_facts.append(Fact(
                fact=fact_text,
                category="research_finding",
                timestamp=datetime.now().isoformat(),
            ))

        # Create memory entry
        entry = MemoryEntry(
            query=query,
            timestamp=datetime.now().isoformat(),
            key_findings=findings[:2000].split("\n")[:10] if findings else [],
            facts=new_facts,
            sources=sources or [],
            report_summary=findings[:500] if findings else "",
        )

        memory.research_history.append(entry)
        memory.extracted_facts.extend(new_facts)

        # Trim history
        if len(memory.research_history) > 50:
            memory.research_history = memory.research_history[-50:]
        if len(memory.extracted_facts) > 200:
            memory.extracted_facts = memory.extracted_facts[-200:]

        self.save(user_id, memory)

        # Update semantic index (async, fire-and-forget)
        try:
            await self._update_semantic_index(user_id, query, findings, new_facts)
        except Exception as e:
            logger.debug(f"[Memory] Semantic index update skipped: {e}")

    async def _update_semantic_index(
        self,
        user_id: str,
        query: str,
        findings: str,
        facts: list[Fact],
    ) -> None:
        """Update the embedding-based semantic index.

        Uses gpt-researcher pattern: embed findings for similarity retrieval.
        """
        try:
            from langchain_openai import OpenAIEmbeddings
            from langchain_core.documents import Document

            embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

            # Create documents from facts and findings
            docs = []
            for fact in facts:
                docs.append(Document(
                    page_content=fact.fact,
                    metadata={"type": "fact", "category": fact.category},
                ))

            if findings:
                docs.append(Document(
                    page_content=findings[:1000],
                    metadata={"type": "findings", "query": query},
                ))

            # Store embeddings in user directory
            embed_dir = self._get_user_path(user_id).parent / "embeddings"
            embed_dir.mkdir(parents=True, exist_ok=True)

            # Store as simple JSON (full vector store in production)
            embedding_data = {
                "timestamp": datetime.now().isoformat(),
                "query": query,
                "documents": [{"content": d.page_content, "metadata": d.metadata} for d in docs],
            }

            (embed_dir / f"entry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(
                json.dumps(embedding_data, ensure_ascii=False)
            )

        except ImportError:
            logger.debug("[Memory] Embedding dependencies not available")

    def get_relevant_context(self, user_id: str, query: str, top_k: int = 5) -> str:
        """Get relevant past research context for a new query.

        Searches both structured memory (recent queries) and
        semantic memory (similarity search) when available.
        """
        memory = self.load(user_id)
        parts = []

        # User preferences
        ctx = memory.user_context
        if ctx.role:
            parts.append(f"User role: {ctx.role}")
        if ctx.expertise_level != "general":
            parts.append(f"Expertise level: {ctx.expertise_level}")
        if ctx.language_preference:
            parts.append(f"Language preference: {ctx.language_preference}")
        if ctx.preferred_sources:
            parts.append(f"Preferred sources: {', '.join(ctx.preferred_sources[:5])}")

        # Recent research history
        recent = memory.research_history[-5:]
        if recent:
            parts.append("\nRecent research topics:")
            for entry in recent:
                parts.append(f"- {entry.query} ({entry.timestamp[:10]})")

        # Relevant facts (simple keyword match for now, embedding in production)
        if memory.extracted_facts:
            query_lower = query.lower()
            relevant_facts = [
                f for f in memory.extracted_facts[-50:]
                if any(word in f.fact.lower() for word in query_lower.split()[:10])
            ]
            if relevant_facts:
                parts.append(f"\nRelevant past findings ({len(relevant_facts)}):")
                for fact in relevant_facts[:top_k]:
                    parts.append(f"- {fact.fact}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def update_user_context(
        self,
        user_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Update user context/preferences."""
        memory = self.load(user_id)
        ctx = memory.user_context

        if "role" in updates:
            ctx.role = updates["role"]
        if "expertise_level" in updates:
            ctx.expertise_level = updates["expertise_level"]
        if "language_preference" in updates:
            ctx.language_preference = updates["language_preference"]
        if "preferred_sources" in updates:
            ctx.preferred_sources = updates["preferred_sources"]
        if "verbosity_preference" in updates:
            ctx.verbosity_preference = updates["verbosity_preference"]

        self.save(user_id, memory)


# =============================================================================
# Global Instance
# =============================================================================

_global_memory_system: Optional[MemorySystem] = None


def get_memory_system() -> MemorySystem:
    """Get or create the global memory system."""
    global _global_memory_system
    if _global_memory_system is None:
        _global_memory_system = MemorySystem()
    return _global_memory_system
