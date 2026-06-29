"""
Session Manager for SoulSearcher.

Provides high-level CRUD operations for research sessions.
Wraps the LangGraph checkpointer for persistence and recovery.
"""

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Summary information about a research session."""

    thread_id: str
    status: str  # pending, running, completed, cancelled, failed
    topic: str
    created_at: str
    updated_at: str
    route: str
    has_report: bool
    revision_count: int
    message_count: int
    owner_id: str = ""
    group_id: str = ""
    visibility: str = "private"

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "status": self.status,
            "topic": self.topic,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "route": self.route,
            "has_report": self.has_report,
            "revision_count": self.revision_count,
            "message_count": self.message_count,
            "owner_id": self.owner_id,
            "group_id": self.group_id,
            "visibility": self.visibility,
        }


@dataclass
class SessionState:
    """Full state snapshot of a research session."""

    thread_id: str
    state: dict[str, Any]
    checkpoint_ts: str
    parent_checkpoint_id: Optional[str]
    deepsearch_artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "checkpoint_ts": self.checkpoint_ts,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "state": self._sanitize_state(self.state),
            "deepsearch_artifacts": self.deepsearch_artifacts,
        }

    def _sanitize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Sanitize state for JSON serialization."""
        sanitized = {}
        for k, v in state.items():
            if k == "messages":
                # Convert messages to serializable format
                sanitized[k] = (
                    [
                        {
                            "type": getattr(m, "type", "unknown"),
                            "content": getattr(m, "content", str(m))[:500],
                        }
                        for m in v[:20]  # Limit to last 20 messages
                    ]
                    if isinstance(v, list)
                    else []
                )
            elif k in ("scraped_content", "pending_tool_calls"):
                # Summarize large lists
                sanitized[k] = f"[{len(v)} items]" if isinstance(v, list) else v
            elif k == "deepsearch_artifacts" and isinstance(v, dict):
                sanitized[k] = {
                    "mode": v.get("mode"),
                    "queries_count": len(v.get("queries", []) or []),
                    "has_tree": bool(v.get("research_tree")),
                    "quality_summary": v.get("quality_summary", {}),
                }
            else:
                # Try to include as-is, fall back to string representation
                try:
                    import json

                    json.dumps(v)
                    sanitized[k] = v
                except (TypeError, ValueError):
                    sanitized[k] = str(v)[:200]
        return sanitized


class SessionManager:
    """
    Manages research sessions using LangGraph checkpointer.

    Provides:
    - List all sessions
    - Get session state
    - Resume session
    - Delete session
    """

    def __init__(self, checkpointer):
        """
        Initialize the session manager.

        Args:
            checkpointer: LangGraph checkpointer instance
        """
        self.checkpointer = checkpointer

    def list_sessions(
        self,
        limit: int = 50,
        status_filter: Optional[str] = None,
        user_id_filter: Optional[str] = None,
    ) -> list[SessionInfo]:
        """
        List all sessions.

        Args:
            limit: Maximum sessions to return
            status_filter: Filter by status (optional)

        Returns:
            List of SessionInfo objects
        """
        sessions = []

        try:
            # Get all thread IDs from checkpointer
            # Note: This implementation depends on the checkpointer type
            if hasattr(self.checkpointer, "list"):
                # Postgres checkpointer with list method
                checkpoints = list(self.checkpointer.list({"configurable": {}}))
            elif hasattr(self.checkpointer, "storage"):
                # Memory checkpointer
                checkpoints = []
                for config, checkpoint in self.checkpointer.storage.items():
                    checkpoints.append({"config": config, "checkpoint": checkpoint})
            else:
                logger.warning("Checkpointer does not support listing")
                return []

            seen_threads = set()

            for cp_info in checkpoints[
                : limit * 2
            ]:  # Get extra to account for duplicates
                try:
                    if isinstance(cp_info, tuple):
                        config, checkpoint = cp_info
                    else:
                        config = cp_info.get("config", {})
                        checkpoint = cp_info.get("checkpoint", cp_info)

                    thread_id = None
                    if isinstance(config, dict):
                        thread_id = config.get("configurable", {}).get("thread_id")
                    elif hasattr(config, "configurable"):
                        thread_id = config.configurable.get("thread_id")

                    if not thread_id or thread_id in seen_threads:
                        continue

                    seen_threads.add(thread_id)

                    # Extract state from checkpoint
                    state = {}
                    if hasattr(checkpoint, "checkpoint"):
                        state = checkpoint.checkpoint.get("channel_values", {})
                    elif isinstance(checkpoint, dict):
                        state = checkpoint.get("channel_values", {})

                    if user_id_filter:
                        owner = state.get("user_id")
                        if (
                            not isinstance(owner, str)
                            or owner.strip() != user_id_filter
                        ):
                            continue

                    session_info = self._build_session_info(
                        thread_id, state, checkpoint
                    )

                    # Apply status filter
                    if status_filter and session_info.status != status_filter:
                        continue

                    sessions.append(session_info)

                    if len(sessions) >= limit:
                        break

                except Exception as e:
                    logger.debug(f"Error processing checkpoint: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error listing sessions: {e}")

        return sessions

    def get_session(self, thread_id: str) -> Optional[SessionInfo]:
        """
        Get session info by thread ID.

        Args:
            thread_id: Thread identifier

        Returns:
            SessionInfo or None if not found
        """
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint_tuple = self.checkpointer.get_tuple(config)

            if not checkpoint_tuple:
                return None

            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            return self._build_session_info(thread_id, state, checkpoint_tuple)

        except Exception as e:
            logger.error(f"Error getting session {thread_id}: {e}")
            return None

    def get_session_state(self, thread_id: str) -> Optional[SessionState]:
        """
        Get full session state.

        Args:
            thread_id: Thread identifier

        Returns:
            SessionState or None if not found
        """
        try:
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint_tuple = self.checkpointer.get_tuple(config)

            if not checkpoint_tuple:
                return None

            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            checkpoint_ts = ""
            parent_id = None

            if hasattr(checkpoint_tuple, "metadata"):
                metadata = checkpoint_tuple.metadata or {}
                checkpoint_ts = metadata.get("created_at", "")

            if hasattr(checkpoint_tuple, "parent_config"):
                parent_config = checkpoint_tuple.parent_config
                if parent_config:
                    parent_id = parent_config.get("configurable", {}).get(
                        "checkpoint_id"
                    )

            deepsearch_artifacts = self._extract_deepsearch_artifacts(state)

            return SessionState(
                thread_id=thread_id,
                state=state,
                checkpoint_ts=checkpoint_ts,
                parent_checkpoint_id=parent_id,
                deepsearch_artifacts=deepsearch_artifacts,
            )

        except Exception as e:
            logger.error(f"Error getting session state {thread_id}: {e}")
            return None

    def delete_session(self, thread_id: str) -> bool:
        """
        Delete a session and all its checkpoints.

        Args:
            thread_id: Thread identifier

        Returns:
            True if deleted, False otherwise
        """
        try:
            config = {"configurable": {"thread_id": thread_id}}

            # Check if checkpointer supports deletion
            if hasattr(self.checkpointer, "delete"):
                self.checkpointer.delete(config)
                logger.info(f"Deleted session: {thread_id}")
                return True
            elif hasattr(self.checkpointer, "put"):
                # Soft delete by marking as deleted
                checkpoint_tuple = self.checkpointer.get_tuple(config)
                if checkpoint_tuple:
                    state = checkpoint_tuple.checkpoint.get("channel_values", {})
                    state["status"] = "deleted"
                    state["is_complete"] = True
                    # Note: Can't actually delete, just mark
                    logger.info(f"Marked session as deleted: {thread_id}")
                    return True

            logger.warning(f"Checkpointer does not support deletion: {thread_id}")
            return False

        except Exception as e:
            logger.error(f"Error deleting session {thread_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Session Fork (Claude Code pattern — explore alternative paths)
    # ------------------------------------------------------------------

    def fork_session(
        self,
        source_thread_id: str,
        new_thread_id: Optional[str] = None,
        *,
        owner_id: Optional[str] = None,
    ) -> Optional[SessionInfo]:
        """Fork a session to explore an alternative research path.

        Copies the latest checkpoint from the source thread to a new thread_id,
        creating an independent branch.  The fork shares no state with the source
        after creation — each can evolve independently.

        Follows Claude Code's "fork a session to explore alternative approaches"
        pattern.

        Args:
            source_thread_id: The thread to fork from.
            new_thread_id: Optional target thread_id.  Auto-generated if omitted.
            owner_id: Optional owner to assign to the forked thread.

        Returns:
            SessionInfo for the new fork, or None if the source doesn't exist.
        """
        import uuid

        try:
            source_config = {"configurable": {"thread_id": source_thread_id}}
            checkpoint_tuple = self.checkpointer.get_tuple(source_config)

            if not checkpoint_tuple:
                logger.warning(
                    "[SessionManager] Cannot fork — source thread %s not found",
                    source_thread_id,
                )
                return None

            if new_thread_id is None:
                new_thread_id = f"fork_{source_thread_id}_{uuid.uuid4().hex[:8]}"

            # Copy checkpoint state under the new thread_id.
            # We preserve the full channel_values so the fork starts from the
            # same state as the source.
            checkpoint = checkpoint_tuple.checkpoint
            channel_values = dict(checkpoint.get("channel_values", {}))

            # Tag the fork for traceability
            channel_values["forked_from"] = source_thread_id
            channel_values["forked_at"] = (
                __import__("datetime").datetime.utcnow().isoformat()
            )
            if owner_id:
                channel_values["user_id"] = owner_id

            metadata = {}
            if hasattr(checkpoint_tuple, "metadata") and checkpoint_tuple.metadata:
                metadata = dict(checkpoint_tuple.metadata)
            metadata["forked_from"] = source_thread_id
            metadata["forked_at"] = (
                __import__("datetime").datetime.utcnow().isoformat()
            )

            fork_config = {"configurable": {"thread_id": new_thread_id}}

            if hasattr(self.checkpointer, "put"):
                self.checkpointer.put(
                    fork_config,
                    {"channel_values": channel_values},
                    metadata,
                    {},
                )
                logger.info(
                    "[SessionManager] Forked %s → %s", source_thread_id, new_thread_id
                )
            else:
                logger.warning(
                    "[SessionManager] Checkpointer does not support put — fork skipped"
                )
                return None

            return self._build_session_info(new_thread_id, channel_values, checkpoint_tuple)

        except Exception as e:
            logger.error(
                "[SessionManager] Fork failed: %s → %s: %s",
                source_thread_id,
                new_thread_id or "?",
                e,
            )
            return None

    def can_resume(self, thread_id: str) -> tuple[bool, str]:
        """
        Check if a session can be resumed.

        Args:
            thread_id: Thread identifier

        Returns:
            Tuple of (can_resume, reason)
        """
        session = self.get_session(thread_id)
        if not session:
            return False, "Session not found"

        if session.status == "completed":
            return False, "Session already completed"

        if session.status == "deleted":
            return False, "Session has been deleted"

        if session.status == "running":
            return False, "Session is currently running"

        return True, "Session can be resumed"

    def build_resume_state(
        self,
        thread_id: str,
        additional_input: Optional[str] = None,
        update_state: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Build a restored state payload for session resumption.

        Rehydrates deepsearch artifacts into top-level fields so graph execution
        can continue from collected context instead of starting from scratch.
        """
        session_state = self.get_session_state(thread_id)
        if not session_state:
            return None

        restored = deepcopy(session_state.state)
        artifacts = session_state.deepsearch_artifacts or {}

        if isinstance(update_state, dict):
            restored.update(update_state)

        if additional_input:
            restored["resume_input"] = additional_input

        if artifacts:
            restored["deepsearch_artifacts"] = artifacts
            if artifacts.get("queries") and not restored.get("research_plan"):
                restored["research_plan"] = list(artifacts.get("queries", []))
            if artifacts.get("research_tree") and not restored.get("research_tree"):
                restored["research_tree"] = artifacts.get("research_tree")
            if artifacts.get("plan_graph") and not restored.get("plan_graph"):
                restored["plan_graph"] = artifacts.get("plan_graph")
            if artifacts.get("plan_events") and not restored.get("plan_events"):
                restored["plan_events"] = list(artifacts.get("plan_events", []))
            if artifacts.get("quality_summary") and not restored.get("quality_summary"):
                restored["quality_summary"] = artifacts.get("quality_summary")
            if artifacts.get("query_coverage") and not restored.get("query_coverage"):
                restored["query_coverage"] = artifacts.get("query_coverage")
            if artifacts.get("freshness_summary") and not restored.get(
                "freshness_summary"
            ):
                restored["freshness_summary"] = artifacts.get("freshness_summary")

        restored["resumed_from_checkpoint"] = True
        restored["resumed_at"] = datetime.utcnow().isoformat()
        return restored

    def _build_session_info(
        self,
        thread_id: str,
        state: dict[str, Any],
        checkpoint_tuple: Any,
    ) -> SessionInfo:
        """Build SessionInfo from state and checkpoint."""
        status = state.get("status", "unknown")
        if state.get("is_complete"):
            status = "completed"
        elif state.get("is_cancelled"):
            status = "cancelled"

        topic = state.get("input", "")[:100]
        route = state.get("route", "unknown")
        has_report = bool(state.get("final_report"))
        revision_count = int(state.get("revision_count", 0))

        messages = state.get("messages", [])
        message_count = len(messages) if isinstance(messages, list) else 0
        owner_id = str(state.get("user_id") or state.get("owner_id") or "").strip()
        group_id = str(state.get("group_id") or "").strip()
        visibility = str(state.get("visibility") or "private").strip() or "private"

        created_at = state.get("started_at", "")
        updated_at = state.get("ended_at", "")

        # Try to get timestamps from checkpoint metadata
        if hasattr(checkpoint_tuple, "metadata"):
            metadata = checkpoint_tuple.metadata or {}
            if not created_at:
                created_at = metadata.get("created_at", "")

        return SessionInfo(
            thread_id=thread_id,
            status=status,
            topic=topic,
            created_at=created_at,
            updated_at=updated_at,
            route=route,
            has_report=has_report,
            revision_count=revision_count,
            message_count=message_count,
            owner_id=owner_id,
            group_id=group_id,
            visibility=visibility,
        )

    def _extract_deepsearch_artifacts(self, state: dict[str, Any]) -> dict[str, Any]:
        """Extract canonical deepsearch artifacts from state snapshot."""
        if not isinstance(state, dict):
            return {}

        artifacts = state.get("deepsearch_artifacts")
        scraped_content = state.get("scraped_content", [])
        final_report = state.get("final_report") or state.get("draft_report") or ""

        def _maybe_extract_sources() -> list[dict[str, Any]]:
            if not isinstance(scraped_content, list) or not scraped_content:
                return []
            try:
                from agent.workflows.evidence_extractor import extract_message_sources

                return extract_message_sources(scraped_content)
            except Exception:
                return []

        def _maybe_extract_claims() -> list[dict[str, Any]]:
            if not isinstance(final_report, str) or not final_report.strip():
                return []
            scraped_list = scraped_content if isinstance(scraped_content, list) else []
            passages_list: Optional[list[dict[str, Any]]] = None
            try:
                from agent.workflows.claim_verifier import ClaimVerifier

                verifier = ClaimVerifier()
                if isinstance(artifacts, dict):
                    raw_passages = artifacts.get("passages")
                    if isinstance(raw_passages, list) and raw_passages:
                        passages_list = raw_passages

                if not scraped_list and not passages_list:
                    return []

                checks = verifier.verify_report(
                    final_report,
                    scraped_list,
                    passages=passages_list,
                )
                claims: list[dict[str, Any]] = []
                for check in checks:
                    claims.append(
                        {
                            "claim": check.claim,
                            "status": check.status.value,
                            "evidence_urls": check.evidence_urls,
                            "evidence_passages": check.evidence_passages,
                            "score": check.score,
                            "notes": check.notes,
                        }
                    )
                return claims
            except Exception:
                return []

        if isinstance(artifacts, dict):
            enriched = dict(artifacts)
            enriched.setdefault("fetched_pages", [])
            enriched.setdefault("passages", [])
            state_todos = state.get("research_todos")
            if "research_todos" not in enriched and isinstance(state_todos, list):
                enriched["research_todos"] = state_todos
            state_todo_summary = state.get("todo_summary")
            if "todo_summary" not in enriched and isinstance(state_todo_summary, dict):
                enriched["todo_summary"] = state_todo_summary
            state_plan_graph = state.get("plan_graph")
            if "plan_graph" not in enriched and isinstance(state_plan_graph, dict):
                enriched["plan_graph"] = state_plan_graph
            if enriched.get("plan_graph"):
                try:
                    from agent.workflows.plan_graph import summarize_plan_graph

                    enriched["plan_summary"] = summarize_plan_graph(enriched["plan_graph"])
                except Exception:
                    enriched["plan_summary"] = {}
            graph_events = (
                enriched.get("plan_graph", {}).get("events")
                if isinstance(enriched.get("plan_graph"), dict)
                else None
            )
            if isinstance(graph_events, list):
                enriched["plan_events"] = [item for item in graph_events if isinstance(item, dict)]
            elif "plan_events" not in enriched:
                state_plan_events = state.get("plan_events")
                if isinstance(state_plan_events, list):
                    enriched["plan_events"] = state_plan_events
            if enriched.get("research_todos") and not enriched.get("todo_summary"):
                try:
                    from agent.workflows.research_todo import summarize_todos

                    enriched["todo_summary"] = summarize_todos(
                        enriched.get("research_todos", [])
                    )
                except Exception:
                    enriched["todo_summary"] = {}
            if "sources" not in enriched:
                sources = _maybe_extract_sources()
                if sources:
                    enriched["sources"] = sources
            if "claims" not in enriched:
                claims = _maybe_extract_claims()
                if claims:
                    enriched["claims"] = claims
            return enriched

        queries = (
            state.get("research_plan", [])
            if isinstance(state.get("research_plan", []), list)
            else []
        )
        research_tree = state.get("research_tree")

        quality_summary: dict[str, Any] = {}
        raw_quality = state.get("quality_summary")
        if isinstance(raw_quality, dict) and raw_quality:
            quality_summary = raw_quality
        else:
            summary_count = len(state.get("summary_notes", []) or [])
            source_count = len(state.get("scraped_content", []) or [])
            quality_overall_score = state.get("quality_overall_score")
            if (
                summary_count > 0
                or source_count > 0
                or quality_overall_score is not None
            ):
                quality_summary = {
                    "summary_count": summary_count,
                    "source_count": source_count,
                    "revision_count": int(state.get("revision_count", 0) or 0),
                    "quality_overall_score": quality_overall_score,
                }

        query_coverage = state.get("query_coverage")
        if not isinstance(query_coverage, dict):
            query_coverage = {}
        if not query_coverage and isinstance(quality_summary, dict):
            nested_coverage = quality_summary.get("query_coverage")
            if isinstance(nested_coverage, dict) and nested_coverage:
                query_coverage = nested_coverage
            else:
                query_coverage_score = quality_summary.get("query_coverage_score")
                if query_coverage_score is not None:
                    try:
                        query_coverage = {"score": float(query_coverage_score)}
                    except (TypeError, ValueError):
                        query_coverage = {}
        freshness_summary = state.get("freshness_summary")
        if not isinstance(freshness_summary, dict):
            freshness_summary = {}
        research_todos = (
            state.get("research_todos")
            if isinstance(state.get("research_todos"), list)
            else []
        )
        todo_summary = (
            state.get("todo_summary")
            if isinstance(state.get("todo_summary"), dict)
            else {}
        )
        if research_todos and not todo_summary:
            try:
                from agent.workflows.research_todo import summarize_todos

                todo_summary = summarize_todos(research_todos)
            except Exception:
                todo_summary = {}
        plan_graph = (
            state.get("plan_graph")
            if isinstance(state.get("plan_graph"), dict)
            else {}
        )
        graph_events = plan_graph.get("events")
        if isinstance(graph_events, list):
            plan_events = graph_events
        elif isinstance(state.get("plan_events"), list):
            plan_events = state.get("plan_events", [])
        else:
            plan_events = []
        plan_summary: dict[str, Any] = {}
        if plan_graph:
            try:
                from agent.workflows.plan_graph import summarize_plan_graph

                plan_summary = summarize_plan_graph(plan_graph)
            except Exception:
                plan_summary = {}

        if (
            not queries
            and not research_tree
            and not quality_summary
            and not query_coverage
            and not freshness_summary
            and not plan_graph
            and not research_todos
        ):
            return {}

        sources = _maybe_extract_sources()
        claims = _maybe_extract_claims()

        return {
            "mode": state.get("deepsearch_mode") or state.get("route") or "deepsearch",
            "queries": queries,
            "research_tree": research_tree,
            "quality_summary": quality_summary,
            "query_coverage": query_coverage,
            "freshness_summary": freshness_summary,
            "plan_graph": plan_graph,
            "plan_events": plan_events,
            "plan_summary": plan_summary,
            "fetched_pages": [],
            "passages": [],
            "sources": sources,
            "claims": claims,
            "research_todos": research_todos,
            "todo_summary": todo_summary,
        }


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def get_session_manager(checkpointer) -> SessionManager:
    """Get or create the global session manager."""
    global _session_manager
    if _session_manager is None or _session_manager.checkpointer != checkpointer:
        _session_manager = SessionManager(checkpointer)
    return _session_manager
