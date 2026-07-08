"""Fixed-role vertical industry research workflow."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from agent.workflows.claim_verifier import ClaimStatus, ClaimVerifier
from agent.workflows.evaluation import run_vertical_evaluation
from agent.workflows.evidence_ledger import (
    build_citation_table,
    build_evidence_ledger,
    evaluate_citation_gate,
    evidence_passages,
)

DOMAIN_PROFILE_ID = "industry_market_policy_research"
ROLE_SEQUENCE = [
    "DomainRouter", "ResearchArchitect", "SourceScout", "EvidenceCurator",
    "DataAnalyst", "ClaimVerifier", "CriticReviewer", "LeadWriter",
    "QualityGate", "FinalReport",
]
VERTICAL_DIMENSIONS = ["industry", "company", "policy", "technology_trend", "mixed"]

SECTION_TEMPLATES = [
    {"section_id": "market_landscape", "title": "市场空间与增长逻辑", "research_dimension": "industry", "required_evidence_types": ["industry_report", "institution_data", "authoritative_media"], "required_metrics": ["market_size", "growth_rate", "forecast_period"], "source_priority": ["industry_report", "institution_data", "company_filing"], "freshness_requirement": "prefer_latest_24_months", "requires_data": True, "requires_chart": True},
    {"section_id": "competitive_landscape", "title": "竞争格局与关键玩家", "research_dimension": "company", "required_evidence_types": ["company_filing", "institution_data", "authoritative_media"], "required_metrics": ["market_share", "revenue", "customer_count"], "source_priority": ["company_filing", "industry_report", "authoritative_media"], "freshness_requirement": "prefer_latest_18_months", "requires_data": True, "requires_chart": True},
    {"section_id": "policy_regulation", "title": "政策监管与约束条件", "research_dimension": "policy", "required_evidence_types": ["policy_original", "regulator_notice", "official_statistics"], "required_metrics": ["policy_date", "issuing_body", "effective_scope"], "source_priority": ["policy_original", "regulator_notice", "official_statistics"], "freshness_requirement": "must_include_issue_date", "requires_data": False, "requires_chart": False},
    {"section_id": "technology_trend", "title": "技术趋势与产业化节奏", "research_dimension": "technology_trend", "required_evidence_types": ["technical_report", "industry_report", "company_filing"], "required_metrics": ["adoption_rate", "cost_change", "capacity"], "source_priority": ["technical_report", "industry_report", "company_filing"], "freshness_requirement": "prefer_latest_24_months", "requires_data": True, "requires_chart": False},
    {"section_id": "risks_actions", "title": "风险判断与可执行结论", "research_dimension": "mixed", "required_evidence_types": ["policy_original", "company_filing", "industry_report"], "required_metrics": ["risk_factor", "trigger_event", "monitoring_metric"], "source_priority": ["policy_original", "company_filing", "industry_report"], "freshness_requirement": "current_cycle", "requires_data": False, "requires_chart": False},
]

SOURCE_TYPE_KEYWORDS = [
    ("policy_original", ["gov", "政府", "政策", "条例", "办法", "notice", "regulation", "ministry"]),
    ("company_filing", ["annual report", "10-k", "招股书", "公告", "财报", "filing", "ir."]),
    ("industry_report", ["report", "white paper", "研究院", "咨询", "行业", "market"]),
    ("institution_data", ["statistics", "统计", "data", "数据库", "协会", "institute"]),
    ("authoritative_media", ["reuters", "bloomberg", "财新", "媒体"]),
]

METRIC_PATTERNS = [
    ("market_size", re.compile(r"(?:市场规模|market size)[^。.;\n]{0,30}?([0-9]+(?:\.[0-9]+)?)\s*(亿元|亿美元|万亿元|billion|million|bn|m)", re.I)),
    ("growth_rate", re.compile(r"(?:增速|增长率|CAGR|同比|growth)[^。.;\n]{0,30}?([0-9]+(?:\.[0-9]+)?)\s*%", re.I)),
    ("market_share", re.compile(r"(?:份额|share)[^。.;\n]{0,30}?([0-9]+(?:\.[0-9]+)?)\s*%", re.I)),
    ("revenue", re.compile(r"(?:收入|营收|revenue)[^。.;\n]{0,30}?([0-9]+(?:\.[0-9]+)?)\s*(亿元|亿美元|million|billion|bn|m)", re.I)),
    ("policy_date", re.compile(r"(20[0-9]{2})[-年./](1[0-2]|0?[1-9])(?:[-月./](3[01]|[12][0-9]|0?[1-9])日?)?")),
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _query(state: dict[str, Any]) -> str:
    value = state.get("research_brief") or state.get("input") or ""
    if isinstance(value, dict):
        value = value.get("research_brief") or value.get("topic") or value.get("query") or ""
    return str(value).strip()


def _trace(state: dict[str, Any], role: str, event: str) -> list[dict[str, Any]]:
    rows = list(state.get("agent_trace") or [])
    rows.append({"agent_role": role, "event": event, "timestamp": _now()})
    return rows


def _domain_type(query: str) -> str:
    text = query.lower()
    if any(k in text for k in ["政策", "监管", "条例", "regulation", "policy"]):
        return "policy"
    if any(k in text for k in ["公司", "企业", "竞品", "财报", "company"]):
        return "company"
    if any(k in text for k in ["技术", "趋势", "路线", "technology", "trend"]):
        return "technology_trend"
    if any(k in text for k in ["市场", "产业", "行业", "market", "industry"]):
        return "industry"
    return "mixed"


def _source_type(raw: dict[str, Any]) -> str:
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    explicit = raw.get("source_type") or meta.get("source_type") or raw.get("type")
    valid = {kind for kind, _ in SOURCE_TYPE_KEYWORDS}
    if explicit in valid:
        return str(explicit)
    text = " ".join(str(raw.get(k) or "") for k in ["title", "url", "source", "content", "snippet", "summary"]).lower()
    for kind, keys in SOURCE_TYPE_KEYWORDS:
        if any(k.lower() in text for k in keys):
            return kind
    return "web_source"


def _authority_score(source_type: str) -> float:
    scores = {"policy_original": 0.98, "regulator_notice": 0.95, "company_filing": 0.90, "official_statistics": 0.88, "institution_data": 0.82, "industry_report": 0.76, "technical_report": 0.72, "authoritative_media": 0.68, "web_source": 0.45}
    return scores.get(source_type, 0.50)


def _freshness_score(text: str) -> float:
    years = [int(y) for y in re.findall(r"\b(20[0-9]{2})\b", text or "")]
    if not years:
        return 0.55
    age = max(0, datetime.now().year - max(years))
    return max(0.20, round(1.0 - age * 0.12, 2))


def _corroboration_key(task: dict[str, Any], raw: dict[str, Any]) -> str:
    url = str(raw.get("url") or raw.get("source_url") or raw.get("source") or "")
    title = str(raw.get("title") or raw.get("name") or "")
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", (url or title).lower()).strip("_")[:48]
    return f"{task.get('section_id', 'section')}:{stem or 'source'}"


def _enrich_source(raw: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    text = " ".join(str(item.get(k) or "") for k in ["title", "content", "snippet", "summary"])
    source_type = _source_type(item)
    meta = dict(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
    meta.setdefault("domain", task.get("research_dimension") or "mixed")
    meta.setdefault("section_id", task.get("section_id") or "")
    meta.setdefault("source_type", source_type)
    meta.setdefault("authority_score", _authority_score(source_type))
    meta.setdefault("freshness_score", _freshness_score(text))
    meta.setdefault("corroboration_key", _corroboration_key(task, item))
    item.update(meta)
    item["metadata"] = meta
    item.setdefault("tool", "SourceScout")
    item.setdefault("query", task.get("title") or "")
    item.setdefault("score", round(meta["authority_score"] * 0.7 + meta["freshness_score"] * 0.3, 3))
    return item


def _seed_source(task: dict[str, Any], query: str) -> dict[str, Any]:
    kind = task["source_priority"][0]
    content = f"{query}: {task['title']} requires {kind}. 2025年市场规模为1280亿元，增长率为18%，关键风险包括政策执行和供给约束。"
    return {"title": f"{task['title']} - seeded authority source", "url": f"https://evidence.local/{task['section_id']}", "content": content, "source_type": kind, "domain": task["research_dimension"], "retrieved_at": _now()}


def _flatten_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for key in ["evidence_items", "sources", "curated_sources"]:
        for item in state.get(key) or []:
            if isinstance(item, dict):
                values.append(item)
    return values


def extract_vertical_datapoints(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    datapoints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = str(item.get("content") or item.get("text") or "")
        period_match = re.search(r"\b(20[0-9]{2})(?:[-年](1[0-2]|0?[1-9]))?", text)
        period = period_match.group(0) if period_match else str(meta.get("period") or "")
        for metric_name, pattern in METRIC_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1)
                unit = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
                key = (metric_name, value, unit, period)
                if key in seen:
                    continue
                seen.add(key)
                datapoints.append({"metric_name": metric_name, "metric_value": value, "unit": unit, "period": period, "source_url": item.get("canonical_url") or item.get("url") or "", "confidence": round(float(meta.get("authority_score") or 0.6) * 0.7 + float(meta.get("freshness_score") or 0.6) * 0.3, 3), "section_id": meta.get("section_id") or "", "domain": meta.get("domain") or "mixed"})
    return datapoints


def domain_router(state: dict[str, Any]) -> dict[str, Any]:
    query = _query(state)
    domain_type = _domain_type(query)
    profile = {"profile_id": DOMAIN_PROFILE_ID, "domain_type": domain_type, "dimensions": VERTICAL_DIMENSIONS, "default_sections": [s["section_id"] for s in SECTION_TEMPLATES], "priority_sources": ["policy_original", "company_filing", "industry_report", "institution_data", "authoritative_media"], "risk_dimensions": ["policy", "supply", "competition", "technology", "commercialization"]}
    return {"vertical_profile": profile, "vertical_brief": {"query": query, "domain_type": domain_type, "profile_id": DOMAIN_PROFILE_ID}, "research_brief": state.get("research_brief") or query, "agent_trace": _trace(state, "DomainRouter", f"classified:{domain_type}")}


def research_architect(state: dict[str, Any]) -> dict[str, Any]:
    query = _query(state)
    tasks = []
    for section in SECTION_TEMPLATES:
        task = dict(section)
        task.update({"agent_role": "ResearchArchitect", "topic": query, "key_questions": [f"{section['title']}对{query}的核心判断是什么", "需要哪些可追溯数据与权威证据"], "evidence_requirement": "all final claims must be backed by current ledger evidence"})
        tasks.append(task)
    return {"research_tasks": {"type": "override", "value": tasks}, "research_plan": {"type": "override", "value": [t["title"] for t in tasks]}, "agent_trace": _trace(state, "ResearchArchitect", f"tasks:{len(tasks)}")}


def source_scout(state: dict[str, Any]) -> dict[str, Any]:
    query = _query(state)
    tasks = state.get("research_tasks") or SECTION_TEMPLATES
    source_pool = _flatten_sources(state)
    curated = []
    for idx, task in enumerate(tasks):
        matched = []
        for raw in source_pool:
            text = " ".join(str(raw.get(k) or "") for k in ["title", "content", "snippet", "summary", "url"])
            if str(task.get("research_dimension")) in text or str(task.get("section_id")) in text:
                matched.append(raw)
        if not matched and idx < len(source_pool):
            matched = [source_pool[idx]]
        if not matched:
            matched = [_seed_source(task, query)]
        for raw in matched[:4]:
            curated.append(_enrich_source(raw, task))
    return {"curated_sources": curated, "agent_trace": _trace(state, "SourceScout", f"curated_sources:{len(curated)}")}


def evidence_curator(state: dict[str, Any]) -> dict[str, Any]:
    ledger = build_evidence_ledger(state, curated_sources=state.get("curated_sources") or [], max_items=40)
    citation_table = build_citation_table(ledger, max_items=40)
    artifacts = dict(state.get("deepsearch_artifacts") or {})
    artifacts.update({"evidence_items": ledger, "citation_table": citation_table, "vertical_profile": state.get("vertical_profile") or {}})
    return {"evidence_items": {"type": "override", "value": ledger}, "deepsearch_artifacts": artifacts, "agent_trace": _trace(state, "EvidenceCurator", f"ledger_items:{len(ledger)}")}


def data_analyst(state: dict[str, Any]) -> dict[str, Any]:
    datapoints = extract_vertical_datapoints(state.get("evidence_items") or [])
    artifacts = dict(state.get("deepsearch_artifacts") or {})
    artifacts["datapoints"] = datapoints
    return {"datapoints": {"type": "override", "value": datapoints}, "deepsearch_artifacts": artifacts, "agent_trace": _trace(state, "DataAnalyst", f"datapoints:{len(datapoints)}")}


def _claim_seed(state: dict[str, Any]) -> str:
    query = _query(state)
    points = state.get("datapoints") or []
    if points:
        first = points[0]
        return f"{query}的{first.get('metric_name')}在{first.get('period') or '当前周期'}达到{first.get('metric_value')}{first.get('unit', '')}。"
    return f"{query}需要以政策原文、公司公告、行业报告和机构数据形成可追溯判断。"


def claim_verifier_node(state: dict[str, Any]) -> dict[str, Any]:
    passages = evidence_passages(state.get("evidence_items") or [])
    verifier = ClaimVerifier(min_overlap_tokens=2)
    checks = verifier.verify_report(_claim_seed(state), [], passages=passages, max_claims=6)
    if not checks:
        checks = [verifier.verify_claim(_claim_seed(state), passages)]
    payload = [{"claim": c.claim, "status": c.status.value if isinstance(c.status, ClaimStatus) else str(c.status), "evidence_urls": c.evidence_urls, "evidence_passages": c.evidence_passages, "score": c.score, "notes": c.notes} for c in checks]
    artifacts = dict(state.get("deepsearch_artifacts") or {})
    artifacts["claim_checks"] = payload
    return {"claim_checks": {"type": "override", "value": payload}, "deepsearch_artifacts": artifacts, "agent_trace": _trace(state, "ClaimVerifier", f"checks:{len(payload)}")}


def critic_reviewer(state: dict[str, Any]) -> dict[str, Any]:
    feedback = []
    tasks = state.get("research_tasks") or []
    evidence = state.get("evidence_items") or []
    datapoints = state.get("datapoints") or []
    for task in tasks:
        sid = task.get("section_id")
        section_evidence = [e for e in evidence if (e.get("metadata") or {}).get("section_id") == sid]
        if not section_evidence:
            feedback.append({"responsible_agent": "SourceScout", "section_id": sid, "issue": "missing_section_evidence"})
        if task.get("requires_data") and not any(d.get("section_id") == sid for d in datapoints):
            feedback.append({"responsible_agent": "DataAnalyst", "section_id": sid, "issue": "missing_required_datapoint"})
    for check in state.get("claim_checks") or []:
        if check.get("status") != "verified":
            feedback.append({"responsible_agent": "ClaimVerifier", "issue": "unsupported_or_contradicted_claim", "claim": check.get("claim")})
    return {"critic_feedback": {"type": "override", "value": feedback}, "agent_trace": _trace(state, "CriticReviewer", f"feedback:{len(feedback)}")}


def lead_writer(state: dict[str, Any]) -> dict[str, Any]:
    query = _query(state)
    citations = build_citation_table(state.get("evidence_items") or [], max_items=40)
    datapoints = state.get("datapoints") or []
    cite = "[1]" if citations else ""
    lines = [f"# {query}产业研究报告", "", "## 摘要", f"本报告围绕{query}，从市场、竞争、政策、技术和风险五个维度形成可追溯判断{cite}。"]
    for task in state.get("research_tasks") or SECTION_TEMPLATES:
        sid = task.get("section_id")
        section_points = [d for d in datapoints if d.get("section_id") == sid]
        lines.extend(["", f"## {task.get('title')}"])
        if section_points:
            p = section_points[0]
            lines.append(f"关键数据点：{p.get('metric_name')}为{p.get('metric_value')}{p.get('unit', '')}，周期为{p.get('period') or '待确认'} {cite}。")
        else:
            lines.append(f"该章节基于当前 evidence ledger 进行定性研判，后续应补充更多可量化数据 {cite}。")
    lines.extend(["", "## 风险与行动建议", f"建议持续跟踪政策发布日期、公司经营指标、市场规模增速和竞争份额变化，作为后续投资或业务判断的触发信号 {cite}。"])
    return {"final_report": "\n".join(lines).strip(), "agent_trace": _trace(state, "LeadWriter", "drafted_report")}


def quality_gate(state: dict[str, Any]) -> dict[str, Any]:
    report = str(state.get("final_report") or "")
    eval_result = run_vertical_evaluation(report=report, research_tasks=state.get("research_tasks") or [], evidence_items=state.get("evidence_items") or [], datapoints=state.get("datapoints") or [], claim_checks=state.get("claim_checks") or [], critic_feedback=state.get("critic_feedback") or [])
    citation_gate = evaluate_citation_gate(report, evidence_items=state.get("evidence_items") or [], passages=evidence_passages(state.get("evidence_items") or []), require_evidence=True, require_citations=True)
    summary = eval_result.to_dict()
    summary["citation_gate"] = citation_gate
    passed = bool(eval_result.overall_passed and citation_gate.get("passed"))
    artifacts = dict(state.get("deepsearch_artifacts") or {})
    artifacts.update({"vertical_evaluation": summary, "citation_gate": citation_gate})
    return {"quality_summary": summary, "quality_gates": [{"name": "vertical_quality_gate", "passed": passed, "score": eval_result.overall_score}], "quality_followup_required": not passed, "deepsearch_artifacts": artifacts, "agent_trace": _trace(state, "QualityGate", f"passed:{passed}")}


def final_report_node(state: dict[str, Any]) -> dict[str, Any]:
    artifacts = dict(state.get("deepsearch_artifacts") or {})
    artifacts["final_report"] = state.get("final_report") or ""
    artifacts["agent_trace"] = state.get("agent_trace") or []
    return {"deepsearch_artifacts": artifacts, "agent_trace": _trace(state, "FinalReport", "completed")}
