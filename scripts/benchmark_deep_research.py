#!/usr/bin/env python3
"""Vertical Industry Research Benchmark Runner — curated internal test suite.

Evaluates SoulSearcher's vertical industry research pipeline against a set of curated test questions
spanning three complexity levels.  Runs via the SSE endpoint (remote) or
in-process (asgi).  Produces a JSON report with quality metrics.

Usage:
    python scripts/benchmark_deep_research.py                          # all cases
    python scripts/benchmark_deep_research.py --max-cases 5            # first 5 only
    python scripts/benchmark_deep_research.py --mode remote --model qwen3.6-plus
    python scripts/benchmark_deep_research.py --output /tmp/report.json
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deep_research_benchmark")


# =============================================================================
# Curated Benchmark Dataset
# =============================================================================

BENCHMARK_CASES: list[dict[str, Any]] = [
    # =========================================================================
    # Level 1 — 事实查询（< 5 步，单次检索即可回答）
    # 分布：6 / 22 ≈ 27%
    # =========================================================================
    {
        "id": "l1_01",
        "query": "2024 年诺贝尔物理学奖授予了谁？表彰他们在哪个领域的贡献？",
        "level": 1,
        "category": "物理学",
        "min_chars": 100,
        "min_citations": 1,
    },
    {
        "id": "l1_02",
        "query": "Python 的 GIL（全局解释器锁）在 3.13 版本中发生了什么变化？",
        "level": 1,
        "category": "计算机科学",
        "min_chars": 200,
        "min_citations": 1,
    },
    {
        "id": "l1_03",
        "query": "台积电（TSMC）目前最先进的量产芯片制程是多少纳米？其主要客户有哪些？",
        "level": 1,
        "category": "半导体",
        "min_chars": 150,
        "min_citations": 1,
    },
    {
        "id": "l1_04",
        "query": "世界卫生组织（WHO）《全球空气质量指南》建议的 PM2.5 年均浓度限值是多少？比上一版收紧了多少？",
        "level": 1,
        "category": "环境科学",
        "min_chars": 150,
        "min_citations": 1,
    },
    {
        "id": "l1_05",
        "query": "2024 年比特币第四次减半后，区块奖励降至多少 BTC？减半对主流矿机的盈亏平衡电价产生了什么影响？",
        "level": 1,
        "category": "金融",
        "min_chars": 200,
        "min_citations": 1,
    },
    {
        "id": "l1_06",
        "query": "国际可再生能源署（IRENA）数据显示 2024 年全球光伏新增装机量约多少 GW？排名前三的市场是哪些？",
        "level": 1,
        "category": "能源",
        "min_chars": 150,
        "min_citations": 1,
    },
    # =========================================================================
    # Level 2 — 对比与分析（5-10 步，需要整合多个来源）
    # 分布：10 / 22 ≈ 46%
    # 覆盖 Skill 注入的全部 6 类报告结构：
    #   academic / market_research / comparison / how_to / summary / deep_analysis
    # =========================================================================
    {
        "id": "l2_01",
        "query": "对比 Qwen 3 和 DeepSeek 系列模型在模型架构、推理性能、训练成本和开源策略上的差异",
        "level": 2,
        "category": "人工智能",
        "min_chars": 500,
        "min_citations": 3,
        "report_type": "comparison",
    },
    {
        "id": "l2_02",
        "query": "Rust 相比 C++ 在内存安全和并发编程方面有哪些核心优势？列举至少三个已采用 Rust 重写关键组件的知名开源项目及其迁移效果",
        "level": 2,
        "category": "计算机科学",
        "min_chars": 400,
        "min_citations": 4,
        "report_type": "comparison",
    },
    {
        "id": "l2_03",
        "query": "分析 2024-2025 年全球固态电池产业化进展，包括氧化物/硫化物/聚合物三条技术路线的代表性企业和最新突破，以及距离车规级量产的主要瓶颈",
        "level": 2,
        "category": "材料科学",
        "min_chars": 500,
        "min_citations": 4,
        "report_type": "market_research",
    },
    {
        "id": "l2_04",
        "query": "2025 年全球新能源汽车市场比亚迪与特斯拉竞争态势对比：销量规模、毛利率、海外工厂布局、产品线覆盖四个维度",
        "level": 2,
        "category": "汽车产业",
        "min_chars": 500,
        "min_citations": 4,
        "report_type": "market_research",
    },
    {
        "id": "l2_05",
        "query": "欧盟《人工智能法案》（EU AI Act）采用风险分级监管模式，请说明四个风险等级的定义、对应的合规要求，以及与中国生成式 AI 监管办法的主要差异",
        "level": 2,
        "category": "法律政策",
        "min_chars": 400,
        "min_citations": 3,
        "report_type": "summary",
    },
    {
        "id": "l2_06",
        "query": "比较三种 mRNA 疫苗递送技术（LNP、聚合物纳米颗粒、病毒载体）在体内稳定性、靶向效率和临床安全性方面的差异，各列举一个代表性产品",
        "level": 2,
        "category": "生物医药",
        "min_chars": 400,
        "min_citations": 4,
        "report_type": "academic",
    },
    {
        "id": "l2_07",
        "query": "2025 年全球云计算 IaaS 市场 AWS、Azure、Google Cloud 三强的份额变化、AI 差异化策略和定价模式对比",
        "level": 2,
        "category": "信息技术",
        "min_chars": 500,
        "min_citations": 3,
        "report_type": "market_research",
    },
    {
        "id": "l2_08",
        "query": "从教学方法、学习效果和学生学习动机三个维度，对比分析 AI 自适应学习平台（如 Khanmigo、Duolingo Max）与传统在线教育的差异，并讨论其在教育公平方面的潜力与风险",
        "level": 2,
        "category": "教育",
        "min_chars": 500,
        "min_citations": 3,
        "report_type": "comparison",
    },
    {
        "id": "l2_09",
        "query": "现代高强度钢材（AHSS）、铝合金和碳纤维复合材料在汽车轻量化中的应用对比：减重效果、成本增量、制造工艺难度和可回收性",
        "level": 2,
        "category": "材料科学",
        "min_chars": 400,
        "min_citations": 3,
        "report_type": "comparison",
    },
    {
        "id": "l2_10",
        "query": "从政策目标、补贴机制和技术路线三个角度，对比分析美国《通胀削减法案》（IRA）和欧盟《绿色协议工业计划》对清洁能源产业链的影响",
        "level": 2,
        "category": "能源政策",
        "min_chars": 500,
        "min_citations": 4,
        "report_type": "summary",
    },
    # =========================================================================
    # Level 3 — 深度研究（> 10 步，跨领域综合，需多轮检索与分析）
    # 分布：6 / 22 ≈ 27%
    # =========================================================================
    {
        "id": "l3_01",
        "query": "深入分析 Transformer 架构从 2017 年提出到 2025 年的演进历程，重点讨论自注意力机制、位置编码方案、归一化策略、稀疏注意力、MoE 混合专家等关键创新，以及各变体（BERT、GPT、T5、Vision Transformer、Sora）对后续研究的推动和影响",
        "level": 3,
        "category": "人工智能",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "deep_analysis",
    },
    {
        "id": "l3_02",
        "query": "全面梳理 AI Agent 技术栈从 ReAct、Plan-and-Solve 到多智能体协作的演进历程，对比 LangGraph、CrewAI、AutoGen、OpenAI Swarm 四个框架的设计理念、调度机制、工具生态和适用场景，分析当前技术瓶颈与未来发展方向",
        "level": 3,
        "category": "人工智能",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "deep_analysis",
    },
    {
        "id": "l3_03",
        "query": "分析全球半导体产业链从 2020 到 2025 年的重构过程：中美技术竞争对设备/EDA 的封锁与突破、美国 CHIPS 法案与欧盟芯片法案的政策效果、台积电/三星/英特尔在 3nm 以下制程的竞争态势，以及供应链从台湾/韩国向美国/日本/欧洲的区域化迁移趋势",
        "level": 3,
        "category": "半导体",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "deep_analysis",
    },
    {
        "id": "l3_04",
        "query": "梳理 CRISPR 基因编辑技术从 2012 年发现到 2025 年临床应用的完整脉络：关键技术突破（Cas9、碱基编辑、先导编辑、表观编辑）、重大伦理争议事件（贺建奎事件、人类胚胎编辑国际峰会）、主要国家监管框架差异，以及已获批和在研的临床治疗方案（镰刀型细胞病、β-地中海贫血、癌症免疫治疗）",
        "level": 3,
        "category": "生物医药",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "academic",
    },
    {
        "id": "l3_05",
        "query": "全球气候变化应对政策的演进与成效分析：从《巴黎协定》国家自主贡献（NDC）机制到 COP28 全球盘点决议，重点分析欧盟碳边境调节机制（CBAM）对国际贸易的影响、中国碳市场从试点到全国统一的扩容历程、以及主要经济体 2020-2025 年实际减排数据与承诺目标的差距",
        "level": 3,
        "category": "环境政策",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "academic",
    },
    {
        "id": "l3_06",
        "query": "社交媒体对青少年心理健康影响的综合研究：梳理 2020-2025 年主要纵向研究的核心发现，分析不同平台（TikTok、Instagram、YouTube）的使用模式与焦虑/抑郁/身体意象问题的关联差异，对比各国（美国 KOSA 法案、英国 OSA 法案、中国未成年人网络保护条例）的立法应对措施及其实施效果",
        "level": 3,
        "category": "心理学",
        "min_chars": 800,
        "min_citations": 6,
        "report_type": "academic",
    },
]


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class CaseResult:
    """Result for a single benchmark case."""
    case_id: str
    query: str
    level: int
    final_report_chars: int = 0
    duration_ms: float = 0
    citation_count: int = 0
    quality_score: float = 0.0
    quality_verdict: str = "unknown"
    claim_alignment_rate: float = 0.0
    level2_score: float = 0.0
    level3_score: float = 0.0
    publish_ready: bool = False
    error: str = ""
    report_preview: str = ""


@dataclass
class BenchmarkReport:
    """Aggregate benchmark report."""
    run_id: str = ""
    timestamp: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    results: list[CaseResult] = field(default_factory=list)
    avg_duration_ms: float = 0
    avg_quality_score: float = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total_cases if self.total_cases > 0 else 0


def validate_benchmark_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate benchmark case shape and coverage before expensive execution."""
    errors: list[str] = []
    warnings: list[str] = []
    ids: set[str] = set()
    level_counts: dict[int, int] = {}
    report_types: set[str] = set()
    categories: set[str] = set()

    for idx, case in enumerate(cases):
        case_id = str(case.get("id", "")).strip()
        if not case_id:
            errors.append(f"case[{idx}] missing id")
        elif case_id in ids:
            errors.append(f"duplicate case id: {case_id}")
        else:
            ids.add(case_id)

        query = str(case.get("query", "")).strip()
        if len(query) < 10:
            errors.append(f"{case_id or idx}: query is too short")

        level = case.get("level")
        if level not in {1, 2, 3}:
            errors.append(f"{case_id or idx}: level must be 1, 2, or 3")
        else:
            level_counts[int(level)] = level_counts.get(int(level), 0) + 1

        min_chars = int(case.get("min_chars", 0) or 0)
        min_citations = int(case.get("min_citations", 0) or 0)
        if min_chars <= 0:
            errors.append(f"{case_id or idx}: min_chars must be positive")
        if min_citations < 0:
            errors.append(f"{case_id or idx}: min_citations cannot be negative")
        if level == 3 and min_citations < 4:
            warnings.append(f"{case_id}: level 3 case has low citation threshold")

        report_type = str(case.get("report_type", "")).strip()
        if report_type:
            report_types.add(report_type)
        elif level in {2, 3}:
            warnings.append(f"{case_id}: analytical case missing report_type")

        category = str(case.get("category", "")).strip()
        if category:
            categories.add(category)

    for level in (1, 2, 3):
        if level_counts.get(level, 0) == 0:
            warnings.append(f"benchmark slice has no level {level} cases")

    expected_report_types = {
        "academic",
        "market_research",
        "comparison",
        "summary",
        "deep_analysis",
    }
    missing_report_types = sorted(expected_report_types - report_types)
    if missing_report_types:
        warnings.append(
            "missing expected report types: " + ", ".join(missing_report_types)
        )

    return {
        "errors": errors,
        "warnings": warnings,
        "case_count": len(cases),
        "level_counts": level_counts,
        "category_count": len(categories),
        "report_types": sorted(report_types),
    }


def validate_rubric_definitions() -> dict[str, Any]:
    """Check rubric definitions for duplicate IDs and invalid weights."""
    L1_RUBRIC, L2_RUBRIC = _load_rubric_definitions()

    errors: list[str] = []
    warnings: list[str] = []

    def check(name: str, rubric: list[dict[str, Any]]) -> None:
        seen_items: set[str] = set()
        if not rubric:
            errors.append(f"{name}: rubric is empty")
            return
        for dim in rubric:
            dim_name = str(dim.get("name", "")).strip()
            if not dim_name:
                errors.append(f"{name}: dimension missing name")
            if float(dim.get("weight", 0) or 0) <= 0:
                errors.append(f"{name}.{dim_name}: dimension weight must be positive")
            items = dim.get("items", [])
            if not isinstance(items, list) or not items:
                errors.append(f"{name}.{dim_name}: dimension has no items")
                continue
            for item in items:
                item_id = str(item.get("id", "")).strip()
                if not item_id:
                    errors.append(f"{name}.{dim_name}: item missing id")
                    continue
                if item_id in seen_items:
                    errors.append(f"{name}: duplicate item id {item_id}")
                seen_items.add(item_id)
                if float(item.get("weight", 0) or 0) <= 0:
                    errors.append(f"{name}.{item_id}: item weight must be positive")
                criterion = str(item.get("criterion", "")).strip()
                if len(criterion) < 20:
                    warnings.append(f"{name}.{item_id}: criterion is very short")

    check("L1", L1_RUBRIC)
    check("L2", L2_RUBRIC)
    return {"errors": errors, "warnings": warnings}


def validate_strict_research_guards() -> dict[str, Any]:
    """Pure local checks for strict vertical research guardrails."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        from agent.workflows.evidence_ledger import evaluate_citation_gate
        citation_gate = evaluate_citation_gate(
            "Unsupported but uncited claim.",
            sources=[{
                "url": "https://example.com/current",
                "source_id": "src_current",
                "snippet_hash": "abc123",
                "title": "Current source",
            }],
            evidence_items=[{
                "url": "https://example.com/current",
                "source_id": "src_current",
                "snippet_hash": "abc123",
                "content": "Current-run evidence passage.",
            }],
            passages=[{
                "url": "https://example.com/current",
                "source_id": "src_current",
                "snippet_hash": "abc123",
                "text": "Current-run evidence passage.",
            }],
            require_citations=True,
        )
        if citation_gate.get("passed"):
            errors.append("strict citation gate accepted an uncited evidence-backed report")

        memory_gate = evaluate_citation_gate(
            "Remembered claim [1].",
            sources=[{
                "url": "https://example.com/memory",
                "source_id": "src_memory",
                "snippet_hash": "abc123",
                "source": "memory",
                "requires_current_run_verification": True,
            }],
            evidence_items=[{
                "url": "https://example.com/memory",
                "source_id": "src_memory",
                "snippet_hash": "abc123",
                "source": "memory",
                "content": "Remembered evidence.",
                "requires_current_run_verification": True,
            }],
            passages=[{
                "url": "https://example.com/memory",
                "source_id": "src_memory",
                "snippet_hash": "abc123",
                "text": "Remembered evidence.",
            }],
        )
        if memory_gate.get("passed"):
            errors.append("strict citation gate accepted memory-only citation")
    except Exception as exc:
        errors.append(f"citation guard preflight failed: {exc}")

    try:
        from agent.retrieval.policy import (
            LegacySourceRoutingError,
            build_retrieval_policy,
            reject_legacy_source_routing,
        )
        academic = build_retrieval_policy(
            {
                "allowed_origins": ["public_web"],
                "channels": ["search_api"],
                "methods": ["web_search"],
                "profiles": ["academic"],
            }
        )
        private_external = build_retrieval_policy(
            {
                "allowed_origins": ["public_web", "private_corpus", "external_system"],
                "channels": ["search_api"],
                "methods": ["web_search"],
            },
            user_id="benchmark",
        )
        if "schema_version" in academic or "academic_search" not in academic.get("methods", []):
            errors.append("retrieval policy academic profile did not enable academic_search")
        if "file_upload" not in private_external.get("channels", []):
            errors.append("retrieval policy private corpus did not enable file_upload")
        if "mcp_search" not in private_external.get("methods", []):
            errors.append("retrieval policy external system did not enable mcp_search")
        try:
            reject_legacy_source_routing({"mode": "web_only"})
            errors.append("retrieval policy accepted legacy source_routing mode")
        except LegacySourceRoutingError:
            pass
    except Exception as exc:
        errors.append(f"retrieval policy preflight failed: {exc}")

    try:
        from agent.workflows.evaluation import run_vertical_evaluation
        from agent.workflows.vertical_research import ROLE_SEQUENCE, research_architect

        if ROLE_SEQUENCE != [
            "DomainRouter",
            "ResearchArchitect",
            "SourceScout",
            "EvidenceCurator",
            "DataAnalyst",
            "ClaimVerifier",
            "CriticReviewer",
            "LeadWriter",
            "QualityGate",
            "FinalReport",
        ]:
            errors.append("fixed-role vertical sequence drifted")
        tasks = research_architect({"research_brief": "AI眼镜产业链研究"})["research_tasks"]["value"]
        required = {
            "agent_role",
            "section_id",
            "research_dimension",
            "required_evidence_types",
            "required_metrics",
            "source_priority",
            "freshness_requirement",
            "requires_data",
            "requires_chart",
        }
        if not tasks or not all(required.issubset(task) for task in tasks):
            errors.append("vertical task schema is incomplete")
        eval_result = run_vertical_evaluation(
            report=(
                "# AI眼镜产业研究报告\n\n"
                "## 市场空间与增长逻辑\n市场规模判断 [1]\n\n"
                "## 竞争格局与关键玩家\n竞争判断 [1]\n\n"
                "## 政策监管与约束条件\n政策判断 [1]\n\n"
                "## 技术趋势与产业化节奏\n技术判断 [1]\n\n"
                "## 风险判断与可执行结论\n建议跟踪政策、竞争、供给、技术和监控指标 [1]。"
            ),
            research_tasks=tasks,
            evidence_items=[{
                "url": "https://example.com/report",
                "content": "2025年AI眼镜市场规模达到1280亿元，增长率为18%。",
                "metadata": {
                    "section_id": "market_landscape",
                    "source_type": "industry_report",
                    "authority_score": 0.82,
                    "freshness_score": 0.95,
                },
            }],
            datapoints=[{
                "metric_name": "market_size",
                "metric_value": "1280",
                "unit": "亿元",
                "period": "2025",
                "section_id": "market_landscape",
            }],
            claim_checks=[{"claim": "市场规模达到1280亿元", "status": "verified"}],
            critic_feedback=[],
        )
        if "responsible_agents" not in eval_result.metadata:
            errors.append("vertical quality gate did not expose responsible agent attribution")
    except Exception as exc:
        errors.append(f"vertical workflow preflight failed: {exc}")

    return {"errors": errors, "warnings": warnings}


def _load_rubric_definitions() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load static rubric constants without requiring LLM runtime dependencies."""
    try:
        from agent.workflows.rubric import L1_RUBRIC, L2_RUBRIC

        return L1_RUBRIC, L2_RUBRIC
    except ModuleNotFoundError:
        rubric_path = Path(__file__).resolve().parent.parent / "agent" / "workflows" / "rubric.py"
        tree = ast.parse(rubric_path.read_text(encoding="utf-8"))
        values: dict[str, list[dict[str, Any]]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value_node = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
                value_node = node.value
            else:
                continue
            for name in names:
                if name in {"L1_RUBRIC", "L2_RUBRIC"}:
                    value = ast.literal_eval(value_node)
                    if isinstance(value, list):
                        values[name] = value

        return values.get("L1_RUBRIC", []), values.get("L2_RUBRIC", [])


# =============================================================================
# Remote mode
# =============================================================================

async def run_remote(
    case: dict[str, Any],
    base_url: str,
    model: str = "",
    timeout: float = 600.0,
) -> CaseResult:
    """Run a benchmark case against a remote SoulSearcher server."""
    import httpx

    r = CaseResult(case_id=case["id"], query=case["query"], level=case["level"])
    t0 = time.monotonic()

    try:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": case["query"]}],
            "deepsearch_config": {
                "report_format": "markdown",
                "allow_clarification": False,
                "max_role_followup_iterations": 3,
            },
        }
        if model:
            payload["deepsearch_config"]["smart_llm"] = model

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(f"{base_url}/api/research/sse", json=payload)
            response.raise_for_status()

            sse_text = response.text
            report_content = _extract_final_content(sse_text)

            r.final_report_chars = len(report_content)
            r.citation_count = _count_citations(report_content)
            r.report_preview = report_content[:300]
            r.quality_verdict = "completed"

    except Exception as e:
        r.error = str(e)
        logger.error(f"[{case['id']}] Remote error: {e}")

    r.duration_ms = (time.monotonic() - t0) * 1000
    return r


def _extract_final_content(sse_text: str) -> str:
    """Extract the final report content from SSE stream text."""
    lines = sse_text.strip().split("\n")
    content = ""
    for line in lines:
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                if data.get("type") == "content":
                    content += data.get("data", {}).get("text", "")
            except json.JSONDecodeError:
                continue
    return content.strip()


def _count_citations(text: str) -> int:
    """Count citation markers in text."""
    import re
    markers = set()
    # Match [N] or [N,N] patterns
    for m in re.finditer(r"\[(\d+(?:,\s*\d+)*)\]", text):
        for num in re.findall(r"\d+", m.group(1)):
            markers.add(num)
    return len(markers)


# =============================================================================
# ASGI mode
# =============================================================================

async def run_asgi(
    case: dict[str, Any],
    graph,
    model: str = "",
) -> CaseResult:
    """Run a benchmark case in-process against the compiled graph."""
    r = CaseResult(case_id=case["id"], query=case["query"], level=case["level"])
    t0 = time.monotonic()

    try:
        from agent.core.state import build_initial_state

        initial_state = build_initial_state(input_text=case["query"])
        configurable: dict[str, Any] = {
            "thread_id": f"bench_{case['id']}",
            "allow_clarification": False,
            "report_format": "markdown",
            "max_role_followup_iterations": 3,
        }
        if model:
            configurable["smart_llm"] = model

        final_state = await graph.ainvoke(initial_state, {"configurable": configurable})
        report = final_state.get("final_report", "")
        artifacts = final_state.get("deepsearch_artifacts", {}) or {}
        quality_summary = final_state.get("quality_summary", {}) or {}
        if not isinstance(quality_summary, dict):
            quality_summary = {}
        if isinstance(artifacts, dict):
            artifact_summary = artifacts.get("quality_summary", {})
            if isinstance(artifact_summary, dict) and artifact_summary:
                quality_summary = artifact_summary

        r.final_report_chars = len(report)
        r.citation_count = _count_citations(report)
        r.report_preview = report[:300]
        r.quality_verdict = str(quality_summary.get("overall_verdict") or "completed")
        r.quality_score = float(quality_summary.get("overall_score", 0.0) or 0.0)
        r.claim_alignment_rate = float(quality_summary.get("claim_alignment_rate", 0.0) or 0.0)
        r.level2_score = float(quality_summary.get("level2_score", 0.0) or 0.0)
        r.level3_score = float(quality_summary.get("level3_score", 0.0) or 0.0)
        r.publish_ready = bool(quality_summary.get("publish_ready", False))

        # Run rubric evaluation if report was generated
        if report and len(report) >= case.get("min_chars", 0) and r.quality_score <= 0:
            try:
                from agent.workflows.rubric import run_level1_rubric
                rubric_result = await run_level1_rubric(
                    report, case["query"], config=None, state=final_state,
                )
                r.quality_score = rubric_result.overall_score
                r.quality_verdict = rubric_result.verdict
            except Exception:
                pass
    except Exception as e:
        r.error = str(e)
        logger.error(f"[{case['id']}] ASGI error: {e}")

    r.duration_ms = (time.monotonic() - t0) * 1000
    return r


# =============================================================================
# Main Runner
# =============================================================================

async def run_benchmark(
    cases: list[dict[str, Any]],
    mode: str = "asgi",
    base_url: str = "http://localhost:8002",
    model: str = "",
    max_concurrent: int = 2,
    human_reference_path: str = "",
) -> BenchmarkReport:
    """Run the full benchmark suite."""
    preflight = validate_benchmark_cases(cases)
    rubric_preflight = validate_rubric_definitions()
    report = BenchmarkReport(
        run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        timestamp=datetime.now().isoformat(),
        config={
            "mode": mode,
            "model": model,
            "max_concurrent": max_concurrent,
            "human_reference_path": human_reference_path,
            "preflight": preflight,
            "rubric_preflight": rubric_preflight,
        },
        total_cases=len(cases),
    )

    if preflight["errors"] or rubric_preflight["errors"]:
        raise ValueError(
            "Benchmark preflight failed: "
            + "; ".join(preflight["errors"] + rubric_preflight["errors"])
        )
    for warning in preflight["warnings"] + rubric_preflight["warnings"]:
        logger.warning("[preflight] %s", warning)

    if mode == "auto":
        try:
            from agent.core.graph import create_research_graph
            graph = create_research_graph()
            runner = lambda c: run_asgi(c, graph, model=model)
            logger.info("Auto mode: using ASGI (in-process)")
        except Exception as e:
            logger.warning(f"ASGI unavailable ({e}), falling back to remote: {base_url}")
            runner = lambda c: run_remote(c, base_url, model=model)
    elif mode == "asgi":
        from agent.core.graph import create_research_graph
        graph = create_research_graph()
        runner = lambda c: run_asgi(c, graph, model=model)
    else:
        runner = lambda c: run_remote(c, base_url, model=model)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_one(case: dict) -> CaseResult:
        async with semaphore:
            logger.info(f"[{case['id']}] Starting...")
            r = await runner(case)
            status = "✗" if r.error else "✓"
            logger.info(
                f"[{case['id']}] {status} {r.duration_ms:.0f}ms "
                f"chars={r.final_report_chars} cites={r.citation_count}"
                f" score={r.quality_score:.2f}"
            )
            return r

    results = await asyncio.gather(*[run_one(c) for c in cases])
    report.results = list(results)

    for r in report.results:
        if r.error:
            report.errored += 1
        elif r.publish_ready or r.quality_verdict == "pass":
            report.passed += 1
        elif r.final_report_chars >= 100:
            report.failed += 1
        else:
            report.failed += 1

    durations = [r.duration_ms for r in report.results if not r.error]
    report.avg_duration_ms = sum(durations) / len(durations) if durations else 0
    scores = [r.quality_score for r in report.results if r.quality_score > 0]
    report.avg_quality_score = sum(scores) / len(scores) if scores else 0

    if human_reference_path:
        try:
            calibration = _run_calibration(report, human_reference_path)
            report.config["calibration"] = calibration
        except Exception as e:
            report.config["calibration_error"] = str(e)

    return report


def _run_calibration(report: BenchmarkReport, human_reference_path: str) -> dict[str, Any]:
    from agent.workflows.rubric import get_calibration

    with open(human_reference_path, encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        references = payload
    else:
        references = payload.get("references", []) if isinstance(payload, dict) else []

    reference_map = {
        str(item.get("case_id", "")).strip(): item
        for item in references
        if isinstance(item, dict) and str(item.get("case_id", "")).strip()
    }

    calibration = get_calibration()
    calibration.references.clear()

    for result in report.results:
        reference = reference_map.get(result.case_id)
        if not reference:
            continue
        human_scores = reference.get("human_scores", {})
        llm_scores = {
            "overall": result.quality_score,
            "claim_alignment": result.claim_alignment_rate,
            "level2": result.level2_score,
            "level3": result.level3_score,
        }
        calibration.add_reference(result.case_id, llm_scores, human_scores)

    agreement = calibration.compute_agreement()
    agreement["source"] = human_reference_path
    return agreement


def print_report(report: BenchmarkReport) -> None:
    """Print a formatted benchmark report."""
    print(f"\n{'='*60}")
    print(f"BENCHMARK REPORT  [{report.run_id}]")
    print(f"{'='*60}")
    print(f"Cases:   {report.total_cases} total | {report.passed} passed "
          f"| {report.failed} failed | {report.errored} errored")
    print(f"Pass rate:    {report.pass_rate:.1%}")
    print(f"Avg duration: {report.avg_duration_ms:.0f}ms")
    print(f"Avg quality:  {report.avg_quality_score:.2f}")
    print(f"\n{'Case':<15} {'Lv':<3} {'Status':<10} {'Time':<8} "
          f"{'Chars':<7} {'Cites':<6} {'Score':<6}")
    print("-" * 70)
    for r in report.results:
        status = "ERROR" if r.error else ("PASS" if r.final_report_chars > 0 else "FAIL")
        print(
            f"{r.case_id:<15} {r.level:<3} {status:<10} "
            f"{r.duration_ms:<8.0f}ms {r.final_report_chars:<7} "
            f"{r.citation_count:<6} {r.quality_score:<6.2f}"
        )


def save_report(report: BenchmarkReport, output_path: str) -> None:
    """Save benchmark report as JSON."""
    data = {
        "run_id": report.run_id,
        "timestamp": report.timestamp,
        "config": report.config,
        "total_cases": report.total_cases,
        "passed": report.passed,
        "failed": report.failed,
        "errored": report.errored,
        "pass_rate": report.pass_rate,
        "avg_duration_ms": report.avg_duration_ms,
        "avg_quality_score": report.avg_quality_score,
        "results": [
            {
                "case_id": r.case_id,
                "query": r.query,
                "level": r.level,
                "final_report_chars": r.final_report_chars,
                "duration_ms": r.duration_ms,
                "citation_count": r.citation_count,
                "quality_score": r.quality_score,
                "quality_verdict": r.quality_verdict,
                "claim_alignment_rate": r.claim_alignment_rate,
                "level2_score": r.level2_score,
                "level3_score": r.level3_score,
                "publish_ready": r.publish_ready,
                "error": r.error,
            }
            for r in report.results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved to {output_path}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Vertical Industry Research Benchmark Runner")
    parser.add_argument("--max-cases", type=int, help="Maximum number of cases to run")
    parser.add_argument("--mode", default="asgi", choices=["asgi", "remote", "auto"])
    parser.add_argument("--model", default="", help="Model override")
    parser.add_argument("--output", default="", help="Output JSON path")
    parser.add_argument("--human-reference", default="", help="Optional JSON file containing human reference scores for calibration")
    parser.add_argument("--concurrent", type=int, default=2)
    parser.add_argument(
        "--preflight-strict",
        action="store_true",
        help="Run local strict citation/source/tool guardrail checks and exit",
    )
    parser.add_argument("--url", default="http://localhost:8002",
                        help="Server URL for remote mode")
    args = parser.parse_args()

    if args.preflight_strict:
        result = validate_strict_research_guards()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["errors"]:
            raise SystemExit(1)
        return

    cases = BENCHMARK_CASES[:args.max_cases] if args.max_cases else BENCHMARK_CASES
    logger.info(f"Running {len(cases)} benchmark cases in {args.mode} mode")

    report = asyncio.run(run_benchmark(
        cases=cases,
        mode=args.mode,
        base_url=args.url,
        model=args.model,
        max_concurrent=args.concurrent,
        human_reference_path=args.human_reference,
    ))

    print_report(report)

    output = args.output or f"/tmp/benchmark_{report.run_id}.json"
    save_report(report, output)


if __name__ == "__main__":
    main()
