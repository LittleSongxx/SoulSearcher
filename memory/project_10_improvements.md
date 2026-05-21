---
name: 10-improvements-completed
description: All 10 deep research improvements implemented based on research findings
metadata:
  type: project
---

All 10 improvements implemented on dev4 branch (2026-05-21), strictly following research findings from open_deep_research, gpt-researcher, STORM, Claude Code, and Google Gemini.

**Why:** User requested complete implementation of all 10 improvement suggestions derived from comprehensive research into SOTA Deep Research systems.

**How to apply:** Future work should build on these patterns — don't revert back to the pre-improvement architecture.

## Completed:

**P0-1**: Merged two runtimes — task() sub-agent tool integrated into Supervisor-Worker graph (supervisor.py line 108)

**P0-2**: Context isolation — _enforce_context_budget() in supervisor.py (line 454), _SUPERVISOR_MAX_MESSAGES=40, ConductResearch results capped at 8000 chars

**P1-3**: HITL plan gate — new research_plan.py with Google Gemini "plan→approve→execute" pattern, LangGraph interrupt(), integrated via graph.py

**P1-4**: Task-type model routing — get_model_for_task() in configuration.py with _TASK_FALLBACK_MAP, 6 task-specific model fields

**P1-5+P2-7**: ACI tools + merged ResearchDeep — ConductResearch now has thoroughness (quick/medium/very_thorough), ResearchDeep removed, ResearcherState has thoroughness field

**P2-6**: Unified middleware — shared.py with check_loop, record_token_usage, enforce_context_budget, safe_execute_tool, record_research_to_memory

**P2-8**: Evidence alignment — 6th quality dimension in quality_check.py, _build_evidence_context() extracts source-text pairs for LLM-as-judge

**P3-9**: Prompt file-based management — PromptLoader in agent/prompts/prompt_loader.py, 8 .md files in agent/prompts/deep_research/, resolve_prompt() in agent/core/prompts.py, wired into input_gateway.py, supervisor.py, researcher.py

**P3-10**: Session fork — fork_session() in session_manager.py, /api/research/fork endpoint in main.py with ForkSessionRequest model
