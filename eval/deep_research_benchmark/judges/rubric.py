from __future__ import annotations

import json
import re
from typing import Any

REPORT_JUDGE_PROMPT_VERSION = "report-rubric-v1"
CITATION_JUDGE_PROMPT_VERSION = "citation-v1"
CLAIM_JUDGE_PROMPT_VERSION = "claim-support-v1"
HALLUCINATION_JUDGE_PROMPT_VERSION = "hallucination-v1"


REPORT_RUBRIC_PROMPT = """You are an expert evaluator for Deep Research reports.
Evaluate the report against the task and return ONLY valid JSON.

Task:
{task_json}

Report:
{report}

Score each dimension from 0 to 10:
- coverage: covers all required dimensions and answers the user query
- depth: provides non-trivial analysis, tradeoffs, details, and synthesis
- structure: organized, coherent, and easy to follow
- evidence_quality: uses relevant sources for important factual claims
- freshness: handles time-sensitive requirements when applicable
- overall: professional trustworthiness for decision-making

Also return rubric_pass=true only if coverage>=7, depth>=6, evidence_quality>=7, overall>=7, and no critical missing required dimension exists.

JSON schema:
{{
  "coverage": 0,
  "depth": 0,
  "structure": 0,
  "evidence_quality": 0,
  "freshness": 0,
  "overall": 0,
  "rubric_pass": false,
  "missing_dimensions": [],
  "rationale": "short rationale"
}}
"""


CITATION_JUDGE_PROMPT = """You are judging citation accuracy for a Deep Research report.
Sample at most 5 important cited or source-backed claim candidates, then decide whether the provided source evidence supports each sampled claim.
Return ONLY valid JSON.

Task:
{task_json}

Report:
{report}

Sources and evidence snippets:
{evidence_json}

Return:
{{
  "total_citation_checks": 0,
  "supported_citation_checks": 0,
  "effective_unique_sources": 0,
  "citation_accuracy": null,
  "checks": [
    {{"claim": "...", "source_url": "...", "supported": true, "reason": "..."}}
  ]
}}
"""


CLAIM_JUDGE_PROMPT = """You are judging whether key factual claims in a Deep Research report are supported by the provided sources.
Return ONLY valid JSON.

Task:
{task_json}

Report:
{report}

Sources and evidence snippets:
{evidence_json}

Sample at most 5 important factual claims and classify each as supported, unsupported, contradicted, or not_checkable.
Return:
{{
  "total_claims": 0,
  "supported_claims": 0,
  "unsupported_claims": 0,
  "contradicted_claims": 0,
  "unsupported_claim_rate": null,
  "claims": [
    {{"claim": "...", "status": "supported", "reason": "...", "source_urls": []}}
  ]
}}
"""


HALLUCINATION_JUDGE_PROMPT = """You are an expert hallucination detector for Deep Research reports.
Identify claims in the report that are NOT supported by the provided evidence.
Focus on these hallucination types:
1. unsourced_assertion: factual claims with no supporting evidence
2. fabricated_statistic: invented numbers, percentages, or data points
3. wrong_attribution: claims attributed to wrong sources or entities
4. temporal_error: incorrect dates, timelines, or chronological claims
5. logical_overreach: conclusions that go beyond what the evidence supports

Task:
{task_json}

Report:
{report}

Sources and evidence snippets:
{evidence_json}

Check at most 8 important factual claims in the report against the evidence.
Return ONLY valid JSON:
{{
  "total_claims_checked": 0,
  "hallucination_count": 0,
  "hallucination_rate": null,
  "hallucinations": [
    {{"claim": "...", "type": "unsourced_assertion", "severity": "high", "reason": "...", "source_urls": []}}
  ]
}}
"""


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty judge response")
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = json.loads(fenced.group(1))
        if isinstance(payload, dict):
            return payload

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(raw[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("judge response does not contain a JSON object")
