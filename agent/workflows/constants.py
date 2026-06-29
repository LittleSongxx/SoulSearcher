"""
Shared constants for SoulSearcher workflows.

Single source of truth for domain markers, quality thresholds, claim markers,
and other constants that were previously scattered across multiple modules.

Imported by: claim_verifier, source_quality, quality_gates, sandbox_policy, etc.
"""

from __future__ import annotations

# ---- Sandbox / Security ----

DANGEROUS_COMMANDS: set[str] = {
    "chmod",
    "chown",
    "dd",
    "mkfs",
    "mkswap",
    "mount",
    "mv",
    "reboot",
    "rm",
    "shutdown",
    "sudo",
    "su",
    "systemctl",
    "wget",
}

NETCAT_ALIASES: set[str] = {"nc", "netcat", "ncat", "socat"}

# ---- Source Quality ----

PRIMARY_DOMAIN_MARKERS = (
    ".gov",
    ".edu",
    ".mil",
    ".int",
    "who.int",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "sec.gov",
    "fda.gov",
    "ec.europa.eu",
    "europa.eu",
    "un.org",
    "nist.gov",
    "nih.gov",
    "arxiv.org",
)

LOW_VALUE_DOMAIN_MARKERS = (
    "medium.com",
    "substack.com",
    "quora.com",
    "reddit.com",
    "pinterest.",
    "facebook.com",
    "twitter.com",
    "x.com",
)

OFFICIAL_HINTS = (
    "official",
    "press release",
    "annual report",
    "regulatory",
    "filing",
    "white paper",
    "documentation",
    "官方",
    "公告",
    "年报",
    "监管",
    "文件",
)

# ---- Claim Verification ----

NEGATION_MARKERS = (
    "not",
    "no",
    "never",
    "without",
    "而非",
    "不是",
    "没有",
    "并非",
    "未",
)

UP_MARKERS = (
    "increase",
    "increased",
    "grow",
    "growth",
    "up",
    "rise",
    "rose",
    "增长",
    "上升",
)

DOWN_MARKERS = (
    "decrease",
    "decreased",
    "decline",
    "down",
    "fell",
    "drop",
    "下降",
    "减少",
)

STOPWORDS: set[str] = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "into",
    "were",
    "was",
    "are",
    "for",
    "has",
    "have",
    "had",
    "will",
    "about",
    "在",
    "是",
    "了",
    "和",
    "与",
    "对",
    "将",
    "及",
}

CLAIM_MARKERS = (
    "research",
    "study",
    "report",
    "data",
    "according to",
    "shows",
    "found",
    "研究",
    "报告",
    "数据显示",
    "统计",
)

META_CLAIM_PATTERNS = (
    r"^(?:本报告|本文|本研究|本分析)(?:将|旨在|试图|基于|重点|主要)",
    r"^以下(?:将|会)",
    r"^(?:this|the) (?:report|paper|analysis|study) (?:will|aims? to|focuses? on|examines?)",
    r"^we (?:will|aim to|analy[sz]e|examine)",
)
