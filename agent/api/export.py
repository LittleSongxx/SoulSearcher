from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.workflows.evidence_extractor import extract_message_sources


class ExportTemplateItem(BaseModel):
    id: str
    name: str
    description: str


class ExportTemplatesResponse(BaseModel):
    templates: list[ExportTemplateItem]


class ExportRequest(BaseModel):
    """Export request for generating reports in various formats."""

    format: str = "html"
    title: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ExportRouterDeps:
    checkpointer: Any
    logger: Any
    require_thread_owner: Callable[[Request, str], None]


def build_export_router(deps: ExportRouterDeps) -> APIRouter:
    router = APIRouter(tags=["export"])

    @router.get("/api/export/templates", response_model=ExportTemplatesResponse)
    async def list_export_templates():
        """
        List available export templates.

        This route must be registered before `/api/export/{thread_id}` so the
        dynamic route does not capture `templates` as a thread_id.
        """
        return {
            "templates": [
                {
                    "id": "default",
                    "name": "Default",
                    "description": (
                        "Standard research report format with SoulSearcher branding"
                    ),
                },
                {
                    "id": "academic",
                    "name": "Academic",
                    "description": (
                        "Formal serif font style for research papers with proper citations"
                    ),
                },
                {
                    "id": "business",
                    "name": "Business",
                    "description": (
                        "Professional business report with gradient header and modern layout"
                    ),
                },
                {
                    "id": "minimal",
                    "name": "Minimal",
                    "description": (
                        "Clean, distraction-free formatting focused on content"
                    ),
                },
            ]
        }

    @router.get("/api/export/{thread_id}")
    async def export_report_endpoint(
        thread_id: str,
        request: Request,
        format: str = "html",
        title: Optional[str] = None,
        template: str = "default",
    ):
        """
        Export a research report for a given thread.

        Args:
            thread_id: Thread ID to export report for
            format: Output format (html, pdf, docx, json)
            title: Optional custom title for the report
            template: Template style (default, academic, business, minimal)
        """
        del template
        if not deps.checkpointer:
            raise HTTPException(status_code=400, detail="No checkpointer configured")

        try:
            deps.require_thread_owner(request, thread_id)
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = deps.checkpointer.get_tuple(config)
            if not checkpoint:
                raise HTTPException(
                    status_code=404,
                    detail=f"No checkpoint found for thread {thread_id}",
                )

            state = checkpoint.checkpoint.get("channel_values", {})
            final_report = state.get("final_report", "")
            if not final_report:
                raise HTTPException(
                    status_code=404, detail="No report found for this thread"
                )

            scraped = state.get("scraped_content", [])
            extracted_sources = []
            try:
                if isinstance(scraped, list):
                    extracted_sources = extract_message_sources(scraped)
            except Exception:
                extracted_sources = []

            source_urls = [
                s.get("url")
                for s in extracted_sources
                if isinstance(s, dict)
                and isinstance(s.get("url"), str)
                and s.get("url")
            ]

            report_title = title or "Research Report"
            format_lower = format.lower().strip()

            if format_lower == "json":
                deepsearch_artifacts = state.get("deepsearch_artifacts", {}) or {}
                if not isinstance(deepsearch_artifacts, dict):
                    deepsearch_artifacts = {}

                sources_payload = deepsearch_artifacts.get("sources")
                if not isinstance(sources_payload, list):
                    sources_payload = extracted_sources

                claims_payload = deepsearch_artifacts.get("claims")
                if not isinstance(claims_payload, list):
                    claims_payload = []
                    try:
                        from agent.workflows.claim_verifier import ClaimVerifier

                        scraped_list = scraped if isinstance(scraped, list) else []
                        passages_payload = deepsearch_artifacts.get("passages")
                        passages_list = (
                            passages_payload
                            if isinstance(passages_payload, list)
                            else None
                        )

                        if (
                            (scraped_list or passages_list)
                            and isinstance(final_report, str)
                            and final_report.strip()
                        ):
                            verifier = ClaimVerifier()
                            checks = verifier.verify_report(
                                final_report,
                                scraped_list,
                                passages=passages_list,
                            )
                            claims_payload = [
                                {
                                    "claim": c.claim,
                                    "status": c.status.value,
                                    "evidence_urls": c.evidence_urls,
                                    "evidence_passages": c.evidence_passages,
                                    "score": c.score,
                                    "notes": c.notes,
                                }
                                for c in checks
                            ]
                    except Exception:
                        claims_payload = []

                quality_payload = deepsearch_artifacts.get("quality_summary")
                if not isinstance(quality_payload, dict):
                    quality_payload = state.get("quality_summary", {}) or {}
                    if not isinstance(quality_payload, dict):
                        quality_payload = {}

                return JSONResponse(
                    status_code=200,
                    content={
                        "thread_id": thread_id,
                        "title": report_title,
                        "report": final_report,
                        "research_brief": deepsearch_artifacts.get(
                            "research_brief", {}
                        ),
                        "sources": sources_payload,
                        "evidence_items": deepsearch_artifacts.get(
                            "evidence_items", []
                        ),
                        "citation_annotations": deepsearch_artifacts.get(
                            "citation_annotations", []
                        ),
                        "timeline": deepsearch_artifacts.get("timeline", []),
                        "supervisor_decisions": deepsearch_artifacts.get(
                            "supervisor_decisions", []
                        ),
                        "worker_runs": deepsearch_artifacts.get("worker_runs", []),
                        "intermediate_steps": deepsearch_artifacts.get(
                            "intermediate_steps", []
                        ),
                        "continue_requests": deepsearch_artifacts.get(
                            "continue_requests", []
                        ),
                        "claims": claims_payload,
                        "quality": quality_payload,
                        "quality_details": deepsearch_artifacts.get(
                            "quality_details", {}
                        ),
                        "quality_gates": deepsearch_artifacts.get(
                            "quality_gates", []
                        ),
                        "exported_at": datetime.now().isoformat(),
                    },
                    headers={
                        "Content-Disposition": (
                            f'attachment; filename="report_{thread_id}.json"'
                        )
                    },
                )

            if format_lower == "html":
                from tools.export import export_report as do_export

                html_content = do_export(
                    final_report,
                    format="html",
                    title=report_title,
                    thread_id=thread_id,
                    sources=source_urls,
                )
                return StreamingResponse(
                    iter(
                        [
                            (
                                html_content.encode("utf-8")
                                if isinstance(html_content, str)
                                else html_content
                            )
                        ]
                    ),
                    media_type="text/html",
                    headers={
                        "Content-Disposition": (
                            f'inline; filename="report_{thread_id}.html"'
                        )
                    },
                )

            if format_lower == "pdf":
                try:
                    from tools.export import export_report as do_export

                    pdf_bytes = do_export(
                        final_report,
                        format="pdf",
                        title=report_title,
                        thread_id=thread_id,
                        sources=source_urls,
                    )
                    return StreamingResponse(
                        iter(
                            [
                                (
                                    pdf_bytes
                                    if isinstance(pdf_bytes, bytes)
                                    else pdf_bytes.encode("utf-8")
                                )
                            ]
                        ),
                        media_type="application/pdf",
                        headers={
                            "Content-Disposition": (
                                f'attachment; filename="report_{thread_id}.pdf"'
                            )
                        },
                    )
                except ImportError as exc:
                    raise HTTPException(
                        status_code=501,
                        detail=f"PDF export requires WeasyPrint: {exc}",
                    ) from exc

            if format_lower in ("docx", "doc"):
                try:
                    from tools.export import export_report as do_export

                    docx_bytes = do_export(
                        final_report,
                        format="docx",
                        title=report_title,
                        thread_id=thread_id,
                        sources=source_urls,
                    )
                    return StreamingResponse(
                        iter(
                            [
                                (
                                    docx_bytes
                                    if isinstance(docx_bytes, bytes)
                                    else docx_bytes.encode("utf-8")
                                )
                            ]
                        ),
                        media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                        headers={
                            "Content-Disposition": (
                                f'attachment; filename="report_{thread_id}.docx"'
                            )
                        },
                    )
                except ImportError as exc:
                    raise HTTPException(
                        status_code=501,
                        detail=f"DOCX export requires python-docx: {exc}",
                    ) from exc

            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: {format}. Use html, pdf, docx, or json.",
            )

        except HTTPException:
            raise
        except Exception as exc:
            deps.logger.error(
                "Export error for thread %s: %s", thread_id, exc, exc_info=True
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return router
