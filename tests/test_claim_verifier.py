from agent.workflows.claim_verifier import ClaimStatus, ClaimVerifier


def test_claim_without_matching_evidence_is_unsupported():
    verifier = ClaimVerifier()
    report = "2024年该公司营收增长了20%，并在海外市场创下历史新高。"
    scraped_content = [
        {
            "query": "company update",
            "results": [
                {
                    "url": "https://example.com/product",
                    "summary": "The company launched a new product line for developers.",
                }
            ],
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.UNSUPPORTED


def test_claim_with_conflicting_evidence_is_contradicted():
    verifier = ClaimVerifier()
    report = "The company's revenue increased in 2024 according to the annual report."
    scraped_content = [
        {
            "query": "revenue trend",
            "results": [
                {
                    "url": "https://example.com/earnings?utm_source=test",
                    "summary": "The company's revenue did not increase in 2024 and decreased by 5%.",
                }
            ],
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.CONTRADICTED
    assert checks[0].evidence_urls == ["https://example.com/earnings"]


def test_claim_with_matching_passage_attaches_passage_level_evidence():
    verifier = ClaimVerifier()
    report = "The company's revenue increased in 2024 according to the annual report."
    scraped_content = []
    passages = [
        {
            "url": "https://example.com/earnings?utm_source=test",
            "text": "In 2024, the company's revenue increased by 5% year over year.",
            "snippet_hash": "passage_123",
            "quote": "In 2024, the company's revenue increased by 5% year over year.",
            "heading_path": ["Results"],
        }
    ]

    checks = verifier.verify_report(report, scraped_content, passages=passages)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED
    assert checks[0].evidence_urls == ["https://example.com/earnings"]
    assert checks[0].evidence_passages
    assert checks[0].evidence_passages[0]["snippet_hash"] == "passage_123"


def test_claim_with_matching_search_result_still_used_when_passages_exist():
    verifier = ClaimVerifier()
    report = "The company's revenue increased in 2024 according to the annual report."
    scraped_content = [
        {
            "query": "revenue trend",
            "results": [
                {
                    "url": "https://example.com/earnings?utm_source=test",
                    "summary": "In 2024, the company's revenue increased by 5% year over year.",
                }
            ],
        }
    ]
    passages = [
        {
            "url": "https://example.com/other",
            "text": "The company launched a developer product.",
        }
    ]

    checks = verifier.verify_report(report, scraped_content, passages=passages)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED
    assert checks[0].evidence_urls == ["https://example.com/earnings"]


def test_chinese_claim_matches_chinese_evidence_by_ngrams():
    verifier = ClaimVerifier()
    report = "2026年该市场规模增长了20%，报告显示企业采用率提升。"
    scraped_content = [
        {
            "query": "market report",
            "results": [
                {
                    "url": "https://example.com/report",
                    "summary": "报告显示，2026年该市场规模增长20%，企业采用率继续提升。",
                }
            ],
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED

def test_extract_claims_skips_markdown_and_numbered_structural_headings():
    verifier = ClaimVerifier()
    report = """
## 3.2.3 Cursor：快速原型与治理的权衡

3.2.3 Cursor：快速原型与治理的权衡

3.3 企业级代理平台架构（覆盖层模式）

2026年该市场规模增长了20%，报告显示企业采用率提升。
"""

    claims = verifier.extract_claims(report)

    assert "3.2.3 Cursor：快速原型与治理的权衡" not in claims
    assert "3.3 企业级代理平台架构（覆盖层模式）" not in claims
    assert claims == ["2026年该市场规模增长了20%，报告显示企业采用率提升。"]


def test_extract_claims_skips_table_and_short_structural_titles():
    verifier = ClaimVerifier()
    report = """
| 最佳场景 | 全AWS企业 | 大型代码库 |
| --- | --- | --- |
企业级代理平台架构
The 2026 report shows enterprise adoption increased by 20%.
"""

    claims = verifier.extract_claims(report)

    assert claims == ["The 2026 report shows enterprise adoption increased by 20%."]


def test_contradiction_uses_matching_fragment_not_unrelated_negation():
    verifier = ClaimVerifier()
    claim = "Cursor Enterprise provides SOC 2 certification and SAML single sign-on."
    evidence = (
        "Cursor Enterprise provides SOC 2 certification and SAML single sign-on. "
        "Other tools do not provide the same governance controls."
    )

    assert verifier._is_contradiction(claim, evidence) is False


def test_chinese_regulatory_amount_matches_english_evidence():
    verifier = ClaimVerifier()
    report = "针对禁止类AI系统的违规行为，最高罚款可达3500万欧元或全球年营业额的7%。"
    scraped_content = [
        {
            "results": [
                {
                    "url": "https://example.com/ai-act",
                    "summary": "EU AI Act imposes maximum fines of €35 million or 7% of global annual turnover for prohibited AI systems.",
                }
            ]
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED


def test_chinese_date_matches_english_date_evidence():
    verifier = ClaimVerifier()
    report = "这些要求将在2026年8月2日全面强制执行。"
    scraped_content = [
        {
            "results": [
                {
                    "url": "https://example.com/ai-act",
                    "summary": "High-risk AI system obligations apply from 2 August 2026.",
                }
            ]
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED

def test_chinese_ai_chip_packaging_bottleneck_matches_english_evidence():
    verifier = ClaimVerifier()
    report = "至2025年，业界已明确封装环节的瓶颈同样严重，甚至比晶圆制造更为突出。"
    scraped_content = [
        {
            "results": [
                {
                    "url": "https://example.com/ai-chip-supply",
                    "summary": "By 2025, TSMC identified advanced packaging, not wafer production, as the industry's true bottleneck.",
                }
            ]
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED


def test_chinese_chip_export_policy_allies_matches_english_evidence():
    verifier = ClaimVerifier()
    report = "报告特别指出，华盛顿的芯片出口政策变化对盟国的工业和AI发展计划产生了连锁效应，尤其是荷兰、台湾和日本。"
    scraped_content = [
        {
            "results": [
                {
                    "url": "https://example.com/export-controls",
                    "summary": "Washington's policy changes on chip exports have a ripple effect on its allies' industrial and AI development planning, especially the Netherlands, Taiwan and Japan.",
                }
            ]
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED


def test_chinese_quera_quantum_memory_matches_english_evidence():
    verifier = ClaimVerifier()
    report = "QuEra的量子存储器演示：以中性原子量子计算机闻名的QuEra公司，领导的一项研究朝着实用硬件迈出一步。"
    scraped_content = [
        {
            "results": [
                {
                    "url": "https://example.com/quera-qec",
                    "summary": "QuEra-led study points to ultra-high-rate quantum error correction moving closer to practical hardware for neutral atom systems and quantum memory.",
                }
            ]
        }
    ]

    checks = verifier.verify_report(report, scraped_content)

    assert len(checks) == 1
    assert checks[0].status == ClaimStatus.VERIFIED
