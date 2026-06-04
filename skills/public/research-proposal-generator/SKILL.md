---
name: research-proposal-generator
description: Generates research proposals and grant applications based on identified research gaps, with testable hypotheses, methodology design, and structured proposal templates.
version: "1.0.0"
tags: [academic, proposal, writing]
---

# Research Proposal Generator Skill

## Overview

This skill synthesizes findings from literature reviews, trend analyses, and paper decompositions to generate structured research proposals. It identifies genuine research gaps, formulates testable hypotheses, designs preliminary methodologies, and produces grant-ready proposal drafts.

**Prerequisite skills**: This skill works best when the user has already run `systematic-literature-review` or `research-trend-analysis` on the target domain. If those haven't been run, the first phase will conduct rapid literature assessment.

## When to Use This Skill

**Use this skill when:**

- User says "generate a research proposal on [topic]"
- User asks "what should I research in [field]?"
- User wants to draft a grant application or fellowship proposal
- User says "give me research ideas" or "suggest PhD topics"
- After completing a systematic literature review, user says "now what?"
- User wants to turn identified research gaps into actionable projects

## Workflow

### Phase 0: Input Assessment

Check what prior research has been conducted:

- **Has SLR been run?** → Use identified gaps directly
- **Has trend analysis been run?** → Use emerging topics as starting points
- **Has paper decomposition been run?** → Use identified limitations as springboards
- **None of the above?** → Run rapid literature scan first (5-10 papers)

If no prior research exists, briefly acknowledge this and suggest running `systematic-literature-review` or `research-trend-analysis` first for higher-quality proposals. Then proceed with the rapid scan.

### Phase 1: Gap Identification

Synthesize research gaps from available sources:

| Gap Type | How to Identify | Example |
|---|---|---|
| **Methodological Gap** | Methods that are missing or under-explored | "No work combines GNNs with diffusion models" |
| **Empirical Gap** | Datasets, benchmarks, or evaluations not done | "Method only tested on English; multilingual performance unknown" |
| **Theoretical Gap** | Missing formal analysis or guarantees | "Convergence proven for convex case only; non-convex is open" |
| **Application Gap** | Domain or problem not addressed | "Technique used for images but not for video or 3D" |
| **Scale Gap** | Works on small scale but not large scale | "O(n²) complexity; sub-quadratic alternatives needed" |
| **Integration Gap** | No work combining two complementary approaches | "RL + symbolic reasoning both exist, but not together" |

Rank gaps by:
1. **Feasibility**: Can this be addressed with available resources?
2. **Impact**: How significant would addressing this gap be?
3. **Novelty**: Is anyone else already working on this?

### Phase 2: Hypothesis Generation

For each top-ranked gap, generate concrete, testable hypotheses:

```
Gap: No work combines approach A with technique B

Hypothesis 1: "Combining A with B improves performance on task X by at least Y% over A alone"
  - Rationale: A excels at Z, B excels at W, task X requires both
  - Falsifiability: If combined A+B doesn't outperform A alone on metric M, hypothesis is rejected
  - Preliminary evidence: [citations supporting the synergy]

Hypothesis 2: "The combination of A and B is most beneficial when [condition C]"
  - Rationale: Based on analyzing when A or B individually fail
  - Falsifiability: ...
```

A good hypothesis must be:
- **Testable**: Can be empirically verified or falsified
- **Specific**: Names the method, metric, and expected improvement
- **Grounded**: Based on identified gaps, not random guesses
- **Significant**: Addresses a meaningful research question

Generate 3-5 hypotheses. Explain why each was chosen.

### Phase 3: Methodology Design

For the best 1-2 hypotheses, design a preliminary methodology:

```markdown
## Proposed Methodology

### Research Design
[Overall approach: empirical, theoretical, systems, mixed]

### Datasets
- **Primary**: [dataset name] — [why this dataset]
- **Validation**: [dataset name] — [for robustness check]
- **Ablation testbed**: [synthetic/controlled data] — [for isolating variables]

Baselines to compare against:
1. [Baseline 1] — [why chosen]
2. [Baseline 2]
3. ...

### Evaluation Protocol
- **Primary metric**: [metric name] — [justification]
- **Secondary metrics**: [list]
- **Statistical testing**: [bootstrap, t-test, etc.]
- **Ablation studies**: [which components to ablate]

### Expected Outcomes
- Best case: [what success looks like]
- Base case: [minimum publishable result]
- Failure mode: [when to pivot or abandon]
```

### Phase 4: Proposal Assembly

Generate a complete proposal following standard academic structure:

```markdown
# Research Proposal: [Title]

## 1. Abstract / Summary
[200-word executive summary]

## 2. Background and Motivation
[What is the problem? Why is it important? What has been done so far?]

## 3. Research Gap
[What specific gap does this proposal address? Why hasn't it been solved?]

## 4. Research Questions and Hypotheses
[Clear, numbered research questions with associated hypotheses]

## 5. Proposed Approach
[Methodology overview, key innovations, why this approach over alternatives]

## 6. Preliminary Evidence / Feasibility
[Any existing results, pilot studies, or theoretical arguments supporting feasibility]

## 7. Evaluation Plan
[How will success be measured? What comparisons will be made?]

## 8. Timeline and Milestones
| Month | Milestone | Deliverable |
|---|---|---|
| 1-3 | Literature review, baseline implementation | Working baseline |
| 4-6 | Core method development | Initial results |
| 7-9 | Full evaluation and ablation | Complete results |
| 10-12 | Write-up and submission | Paper draft |

## 9. Resources Required
- Compute: [GPU hours, cloud budget estimate]
- Data: [any data acquisition needs]
- Software: [libraries, frameworks]
- Collaboration: [expertise needed from other domains]

## 10. Broader Impact
[Potential applications, ethical considerations, societal benefit]

## References
[Key papers supporting the proposal]
```

## Integration with Other Skills

- **After `systematic-literature-review`**: Proposals are grounded in comprehensive literature analysis
- **After `research-trend-analysis`**: Proposals target emerging / underserved areas
- **After `paper-decomposition`**: Proposals address specific methodological limitations
- **With `reproducibility-audit`**: Validate that baseline methods can actually be reproduced before building on them

## Tips for High-Quality Proposals

- **Be specific about the gap**: "We don't know X under condition Y" is better than "X is under-explored"
- **Propose a falsifiable hypothesis**: If the experiment can't disprove your hypothesis, it's not science
- **Include negative results planning**: What will you do if the hypothesis is rejected?
- **Acknowledge limitations upfront**: All proposals have weaknesses; acknowledging them shows rigor
- **Connect to the broader field**: Explain why the community should care about this problem
- **Be realistic about timeline**: A PhD-level proposal should be 3-5 years; a paper should be 3-12 months

## Quality Checklist

- [ ] At least 3 research gaps identified and ranked by feasibility/impact/novelty
- [ ] At least 3 testable hypotheses generated, each with falsification criteria
- [ ] Methodology design includes datasets, baselines, metrics, and statistical tests
- [ ] Timeline is realistic with concrete milestones
- [ ] Resources estimated (compute, data, expertise)
- [ ] Broader impact section addresses ethical and societal implications
- [ ] All claims about prior work are cited
- [ ] Output saved (ensure `mkdir -p /mnt/user-data/outputs`) to `/mnt/user-data/outputs/proposal-{topic-slug}.md`
