---
name: academic-paper-review
description: Use this skill when the user requests to review, analyze, critique, or summarize academic papers, research articles, preprints, or scientific publications. Supports comprehensive structured reviews covering methodology assessment, contribution evaluation, literature positioning, and constructive feedback generation. Trigger on queries involving paper URLs, uploaded PDFs, arXiv links, or requests like "review this paper", "analyze this research", "summarize this study", or "write a peer review".
---

# Academic Paper Review Skill

## Overview

This skill produces structured, peer-review-quality analyses of academic papers and research publications. It follows established academic review standards used by top-tier venues (NeurIPS, ICML, ACL, Nature, IEEE) to provide rigorous, constructive, and balanced assessments.

The review covers **summary, strengths, weaknesses, methodology assessment, contribution evaluation, literature positioning, and actionable recommendations** — all grounded in evidence from the paper itself.

## Core Capabilities

- Parse and comprehend academic papers from uploaded PDFs or fetched URLs
- Generate structured reviews following top-venue review templates
- Assess methodology rigor (experimental design, statistical validity, reproducibility)
- Evaluate novelty and significance of contributions
- Position the work within the broader research landscape via targeted literature search
- Identify limitations, gaps, and potential improvements
- Produce both detailed review and concise executive summary formats
- Support papers in any scientific domain (CS, biology, physics, social sciences, etc.)

## When to Use This Skill

**Always load this skill when:**

- User provides a paper URL (arXiv, DOI, conference proceedings, journal link)
- User uploads a PDF of a research paper or preprint
- User asks to "review", "analyze", "critique", "assess", or "summarize" a research paper
- User wants to understand the strengths and weaknesses of a study
- User requests a peer-review-style evaluation of academic work
- User asks for help preparing a review for a conference or journal submission

## Review Methodology

### Phase 1: Paper Comprehension

Thoroughly read and understand the paper before forming any judgments.

#### Step 1.1: Identify Paper Metadata

Extract and record:

| Field | Description |
|-------|-------------|
| **Title** | Full paper title |
| **Authors** | Author list and affiliations |
| **Venue / Status** | Publication venue, preprint server, or submission status |
| **Year** | Publication or submission year |
| **Domain** | Research field and subfield |
| **Paper Type** | Empirical, theoretical, survey, position paper, systems paper, etc. |

#### Step 1.2: Deep Reading Pass

Read the paper systematically:

1. **Abstract & Introduction** — Identify the claimed contributions and motivation
2. **Related Work** — Note how authors position their work relative to prior art
3. **Methodology** — Understand the proposed approach, model, or framework in detail
4. **Experiments / Results** — Examine datasets, baselines, metrics, and reported outcomes
5. **Discussion & Limitations** — Note any self-identified limitations
6. **Conclusion** — Compare concluded claims against actual evidence presented

#### Step 1.3: Key Claims Extraction

List the paper's main claims explicitly:

```
Claim 1: [Specific claim about contribution or finding]
Evidence: [What evidence supports this claim in the paper]
Strength: [Strong / Moderate / Weak]

Claim 2: [...]
...
```

### Phase 2: Critical Analysis

#### Step 2.1: Literature Context Search

Use web search to understand the research landscape:

```
Search queries:
- "[paper topic] state of the art [current year]"
- "[key method name] comparison benchmark"
- "[authors] previous work [topic]"
- "[specific technique] limitations criticism"
- "survey [research area] recent advances"
```

Use `web_fetch` on key related papers or surveys to understand where this work fits.

#### Step 2.2: Methodology Assessment

Evaluate the methodology using the following framework:

| Criterion | Questions to Ask | Rating |
|-----------|-----------------|--------|
| **Soundness** | Is the approach technically correct? Are there logical flaws? | 1-5 |
| **Novelty** | What is genuinely new vs. incremental improvement? | 1-5 |
| **Reproducibility** | Are details sufficient to reproduce? Code/data available? | 1-5 |
| **Experimental Design** | Are baselines fair? Are ablations adequate? Are datasets appropriate? | 1-5 |
| **Statistical Rigor** | Are results statistically significant? Error bars reported? Multiple runs? | 1-5 |
| **Scalability** | Does the approach scale? Are computational costs discussed? | 1-5 |

#### Step 2.3: Contribution Significance Assessment

Evaluate the significance level:

| Level | Description | Criteria |
|-------|-------------|----------|
| **Landmark** | Fundamentally changes the field | New paradigm, widely applicable breakthrough |
| **Significant** | Strong contribution advancing the state of the art | Clear improvement with solid evidence |
| **Moderate** | Useful contribution with some limitations | Incremental but valid improvement |
| **Marginal** | Minimal advance over existing work | Small gains, narrow applicability |
| **Below threshold** | Does not meet publication standards | Fundamental flaws, insufficient evidence |

#### Step 2.4: Strengths and Weaknesses Analysis

For each strength or weakness, provide:
- **What**: Specific observation
- **Where**: Section/figure/table reference
- **Why it matters**: Impact on the paper's claims or utility

### Phase 3: Evidence Verification

**This phase leverages Weaver's unique claim verification infrastructure to fact-check the paper's claims against independent sources.**

#### Step 3.1: Extract Structured Claims

Use the bundled extraction script to pull structured, verifiable claims from the paper:

```bash
python /mnt/skills/public/academic-paper-review/scripts/extract_claims.py \
  <paper_text_file> \
  --max-claims 80 \
  --output /tmp/claims.json
```

The output is a JSON array where each claim has: `claim_id`, `claim_text`, `claim_type` (numerical/methodological/comparative/definitional/speculative), `verifiability`, and `location`.

#### Step 3.2: Independent Evidence Search

For each **numerical** claim (highest verifiability), search for independent confirmation:

```
For each numerical claim:
  1. Identify the key metric and value
  2. Search: "[metric] [paper topic] benchmark results" via web_search
  3. Search: "[paper topic] [dataset name] leaderboard" via web_search
  4. Fetch any independent benchmark reports, leaderboards, or meta-analyses
  5. Record whether the claimed value is: confirmed / contradicted / cannot be independently verified
```

Prioritize claims with `verifiability: "high"`. At minimum, verify the paper's 3-5 most important numerical claims.

#### Step 3.3: Cross-Reference with Existing Benchmarks

For papers reporting on standard benchmarks:

- Check Papers With Code leaderboards for the reported dataset/task
- Compare reported numbers against the current SOTA
- Flag if reported numbers significantly exceed known SOTA without explanation
- Check if the paper's comparison baselines are the correct/current SOTA methods

#### Step 3.4: Evidence Verification Summary

Add this section to the review output:

```markdown
## Evidence Verification

| Claim | Claimed Value | Independent Evidence | Status |
|---|---|---|---|
| Accuracy on X | 94.2% | Leaderboard shows 93.8-94.5% range | ✓ Consistent |
| Outperforms Y by Z% | +3.5% | Independent benchmark: Y achieves 91.0% | ✓ Confirmed |
| First to achieve W | N/A | Paper from 2023 achieved similar result | ⚠ Disputed |

**Verification Summary**:
- Verified claims: X / Y
- Contradicted claims: A / Y
- Cannot verify (no independent source): B / Y
```

### Phase 4: Reproducibility Assessment

**Leverages Weaver's E2B sandbox for automated code execution and result comparison.**

#### Step 4.1: Check for Code Availability

Search for the paper's code:
```
1. Check paper for GitHub links or "Code available at" statements
2. Search: "github.com [paper title]" via web_search
3. Search Papers With Code for the paper
4. Check the paper's website or supplementary materials
```

If code is found, proceed. If not, note this as a limitation in the review.

#### Step 4.2: Run Reproducibility Audit

If code is available, invoke the `reproducibility-audit` skill or perform a lightweight check:

```bash
# Clone in sandbox
cd /home/user && git clone --depth 1 <repo_url> paper_check

# Check if it has recognizable structure
ls paper_check/
find paper_check/ -name "requirements.txt" -o -name "pyproject.toml" -o -name "README.md" | head -5
```

Even a partial check provides valuable evidence:
- **Code exists but won't run**: Yellow flag — documentation issue
- **Code runs but produces different numbers**: Red flag — potential reproducibility issue
- **No code**: Downgrade reproducibility score to 1/5

#### Step 4.3: Reproducibility Rating

| Score | Criteria |
|---|---|
| **5/5** | Code runs cleanly, reproduces key results within tolerance |
| **4/5** | Code runs with minor fixes, reproduces most results |
| **3/5** | Code available but requires significant effort to reproduce |
| **2/5** | Code partially available or heavily bit-rotted |
| **1/5** | No code or data available |

Add the reproducibility rating to the methodology assessment table.

### Phase 5: Review Synthesis

#### Step 5.1: Assemble the Structured Review

Produce the final review using the template below.

## Review Output Template

```markdown
# Paper Review: [Paper Title]

## Paper Metadata
- **Authors**: [Author list]
- **Venue**: [Publication venue or preprint server]
- **Year**: [Year]
- **Domain**: [Research field]
- **Paper Type**: [Empirical / Theoretical / Survey / Systems / Position]

## Executive Summary

[2-3 paragraph summary of the paper's core contribution, approach, and main findings.
State your overall assessment upfront: what the paper does well, where it falls short,
and whether the contribution is sufficient for the claimed venue/impact level.]

## Summary of Contributions

1. [First claimed contribution — one sentence]
2. [Second claimed contribution — one sentence]
3. [Additional contributions if any]

## Strengths

### S1: [Concise strength title]
[Detailed explanation with specific references to sections, figures, or tables in the paper.
Explain WHY this is a strength and its significance.]

### S2: [Concise strength title]
[...]

### S3: [Concise strength title]
[...]

## Weaknesses

### W1: [Concise weakness title]
[Detailed explanation with specific references. Explain the impact of this weakness on
the paper's claims. Suggest how it could be addressed.]

### W2: [Concise weakness title]
[...]

### W3: [Concise weakness title]
[...]

## Methodology Assessment

| Criterion | Rating (1-5) | Assessment |
|-----------|:---:|------------|
| Soundness | X | [Brief justification] |
| Novelty | X | [Brief justification] |
| Reproducibility | X | [Brief justification] |
| Experimental Design | X | [Brief justification] |
| Statistical Rigor | X | [Brief justification] |
| Scalability | X | [Brief justification] |

## Questions for the Authors

1. [Specific question that would clarify a concern or ambiguity]
2. [Question about methodology choices or alternative approaches]
3. [Question about generalizability or practical applicability]

## Minor Issues

- [Typos, formatting issues, unclear figures, notation inconsistencies]
- [Missing references that should be cited]
- [Suggestions for improved clarity]

## Literature Positioning

[How does this work relate to the current state of the art?
Are key related works cited? Are comparisons fair and comprehensive?
What important related work is missing?]

## Recommendations

**Overall Assessment**: [Accept / Weak Accept / Borderline / Weak Reject / Reject]

**Confidence**: [High / Medium / Low] — [Justification for confidence level]

**Contribution Level**: [Landmark / Significant / Moderate / Marginal / Below threshold]

### Actionable Suggestions for Improvement
1. [Specific, constructive suggestion]
2. [Specific, constructive suggestion]
3. [Specific, constructive suggestion]
```

## Review Principles

### Constructive Criticism
- **Always suggest how to fix it** — Don't just point out problems; propose solutions
- **Give credit where due** — Acknowledge genuine contributions even in flawed papers
- **Be specific** — Reference exact sections, equations, figures, and tables
- **Separate minor from major** — Distinguish fatal flaws from fixable issues

### Objectivity Standards
- ❌ "This paper is poorly written" (vague, unhelpful)
- ✅ "Section 3.2 introduces notation X without formal definition, making the proof in Theorem 1 difficult to follow. Consider adding a notation table after the problem formulation." (specific, actionable)

### Ethical Review Practices
- Do NOT dismiss work based on author reputation or affiliation
- Evaluate the work on its own merits
- Flag potential ethical concerns (bias in datasets, dual-use implications) constructively
- Maintain confidentiality of unpublished work

## Adaptation by Paper Type

| Paper Type | Focus Areas |
|------------|-------------|
| **Empirical** | Experimental design, baselines, statistical significance, ablations, reproducibility |
| **Theoretical** | Proof correctness, assumption reasonableness, tightness of bounds, connection to practice |
| **Survey** | Comprehensiveness, taxonomy quality, coverage of recent work, synthesis insights |
| **Systems** | Architecture decisions, scalability evidence, real-world deployment, engineering contributions |
| **Position** | Argument coherence, evidence for claims, impact potential, fairness of characterizations |

## Common Pitfalls to Avoid

- ❌ Reviewing the paper you wish was written instead of the paper that was submitted
- ❌ Demanding additional experiments that are unreasonable in scope
- ❌ Penalizing the paper for not solving a different problem
- ❌ Being overly influenced by writing quality versus technical contribution
- ❌ Treating absence of comparison to your own work as a weakness
- ❌ Providing only a summary without critical analysis

## Quality Checklist

Before finalizing the review, verify:

- [ ] Paper was read completely (not just abstract and introduction)
- [ ] All major claims are identified and evaluated against evidence
- [ ] At least 3 strengths and 3 weaknesses are provided with specific references
- [ ] The methodology assessment table is complete with ratings and justifications
- [ ] Questions for authors target genuine ambiguities, not rhetorical critiques
- [ ] Literature search was conducted to contextualize the contribution
- [ ] Recommendations are actionable and constructive
- [ ] The overall assessment is consistent with the identified strengths and weaknesses
- [ ] The review tone is professional and respectful
- [ ] Minor issues are separated from major concerns

## Output Format

- Output the complete review in **Markdown** format
- First ensure the output directory exists: `mkdir -p /mnt/user-data/outputs`
- Save the review to `/mnt/user-data/outputs/review-{paper-topic}.md` when working in sandbox
- Present the review to the user using the `present_files` tool

## Advanced Modes

### Mode A: Meta-Review (Comparative Cross-Paper Analysis)

Activate this mode when the user provides 3-5 papers on the same topic and asks for comparison, ranking, or a meta-review.

**Workflow:**

1. Run the standard review (Phases 1-5) for each paper independently
2. Extract a comparison matrix across all papers:

```markdown
## Comparative Analysis Matrix

| Criterion | Paper A | Paper B | Paper C | Paper D |
|---|---|---|---|---|
| **Methodology** | X/5 | X/5 | X/5 | X/5 |
| **Novelty** | X/5 | X/5 | X/5 | X/5 |
| **Reproducibility** | X/5 | X/5 | X/5 | X/5 |
| **Experimental Rigor** | X/5 | X/5 | X/5 | X/5 |
| **Primary Dataset** | Name | Name | Name | Name |
| **Primary Metric** | X% | X% | X% | X% |
| **Key Innovation** | ... | ... | ... | ... |
| **Major Limitation** | ... | ... | ... | ... |

## Rankings

### By Overall Quality
1. Paper [X] — [justification]
2. Paper [Y] — [justification]
...

### By Contribution Significance
1. Paper [X] — [justification]
...

## Consensus Findings
- [What do all papers agree on?]

## Contested Findings
- [Where do papers disagree? What explains the disagreement?]

## Current SOTA Summary
[Based on these papers, what is the current best practice in this area?]

## Recommendations for Future Work
[What gaps remain across all papers? What should the next paper do?]
```

3. Save as `meta-review-{topic-slug}.md`

### Mode B: Reviewer Simulator

Activate when the user asks to simulate a review for a specific venue ("review this as if for NeurIPS", "write an ICML-style review").

**Supported Venues:**

| Venue | Focus Areas | Review Form | Tone |
|---|---|---|---|
| **NeurIPS** | Novelty, empirical rigor, clarity | 4-question form (Summary, Strengths, Weaknesses, Overall) | Constructive but blunt |
| **ICML** | Technical correctness, significance | Similar to NeurIPS, heavier weight on theory | Technical, precise |
| **ACL / EMNLP** | Linguistic contribution, reproducibility | Structured with soundness/novelty/clarity ratings | Constructive |
| **CHI** | Human-centered contribution, evaluation rigor | Contribution type classification + evaluation | Application-focused |
| **CVPR / ICCV** | Visual results, benchmark performance | Results-first evaluation | Metric-focused |
| **Nature / Science** | Broad impact, novelty, accessibility | Short-form, high-rejection-rate | Brief, impactful |
| **IEEE / ACM Trans.** | Technical depth, completeness | Detailed, thorough | Technical, exhaustive |

**Adaptation rules:**

- For machine learning venues (NeurIPS, ICML, ICLR): emphasize novelty, theoretical justification, and fair comparison to SOTA
- For NLP venues (ACL, EMNLP): emphasize linguistic insight, reproducibility, and ethical considerations
- For systems venues: emphasize engineering contribution, scalability, and real-world deployment evidence
- For high-impact journals (Nature, Science): emphasize broad significance, accessibility, and paradigm-shifting potential

**Simulated Review Output:**

```markdown
# [Venue Name] Review: [Paper Title]

## Reviewer Information
- **Reviewer Expertise**: [High/Medium/Low] — [justification]
- **Confidence in Review**: [High/Medium/Low]

## Summary
[As required by venue format]

## Strengths
[Weighted by venue priorities]

## Weaknesses
[Weighted by venue priorities]

## Questions for Authors
[Venue-appropriate questions]

## Overall Recommendation
- [ ] Strong Accept (top 5%)
- [ ] Accept (top 20%)
- [ ] Weak Accept (top 40%)
- [ ] Borderline
- [ ] Reject

## Confidence
[High/Medium/Low] — [explanation]

## Reason for Recommendation
[Venue-aligned justification]

## Private Comments to Area Chair / Editor
[If applicable — notes about ethics concerns, conflicts, or other sensitive issues]
```

## Integration with Other Academic Skills

- **`reproducibility-audit`**: Use for Phase 4 (Reproducibility Assessment). The audit provides concrete execution evidence.
- **`paper-decomposition`**: Use for structured claim extraction and dependency analysis.
- **`systematic-literature-review`**: Use for Phase 2.1 (Literature Context Search) to find related work.
- **`deep-research`**: Load for any paper that requires understanding the broader research context.
- **`science-communication`**: After reviewing, use to create lay summaries, press releases, or social media posts.

## Notes

- This skill complements the `deep-research` skill — load both when the user wants the paper reviewed in the context of the broader field
- For papers behind paywalls, work with whatever content is accessible (abstract, publicly available versions, preprint mirrors)
- Adapt the review depth to the user's needs: a brief assessment for quick triage versus a full review for submission preparation
- When reviewing multiple papers comparatively, maintain consistent criteria across all reviews
- Always disclose limitations of your review (e.g., "I could not verify the proofs in Appendix B in detail")
- In Meta-Review mode, load all papers before starting the comparison
- In Reviewer Simulator mode, state the venue's review criteria in your preamble
- Evidence verification (Phase 3) uses Weaver's `claim_verifier` engine — numerical claims are automatically cross-checked
