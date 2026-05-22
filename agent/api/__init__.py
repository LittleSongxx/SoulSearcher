"""Weaver API router modules — extracted from main.py for maintainability."""

from agent.api.tracing import router as tracing_router
from agent.api.export import router as export_router
from agent.api.documents import router as documents_router

__all__ = ["tracing_router", "export_router", "documents_router"]
