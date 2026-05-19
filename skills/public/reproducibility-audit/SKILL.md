---
name: reproducibility-audit
description: Use this skill when the user wants to assess the reproducibility of an academic paper, verify its reported results, reproduce its experiments, or perform a computational reproducibility audit. Automatically detects code repositories, builds environments in sandbox, runs experiments, and compares outputs against paper claims. Trigger on requests like "check if this paper is reproducible", "reproduce these results", "audit reproducibility", "verify paper claims", or when reviewing a paper that includes code.
---

# Reproducibility Audit Skill

## Overview

This skill performs a structured, automated reproducibility assessment of academic papers. It leverages Weaver's E2B sandbox to actually execute paper code, compares outputs against reported claims, and generates a detailed reproducibility report with an overall rating.

**This is Weaver's unique differentiator** — no other open-source deep research tool can execute paper code in an isolated sandbox for automated reproducibility verification.

## When to Use This Skill

**Use this skill when:**

- User provides a paper URL and asks "is this reproducible?", "check reproducibility", "verify results"
- User wants to know if a paper's code actually produces the reported numbers
- User is reviewing a paper and needs to assess computational reproducibility
- User has found a paper with code and wants to try reproducing it
- The `academic-paper-review` skill identifies reproducibility concerns that need investigation

**Do NOT use when:**

- Paper has no code or data available (report this and stop)
- Paper is purely theoretical with no computational experiments
- The paper's computational requirements exceed sandbox capacity (heavy GPU, large clusters)

## Workflow

### Phase 1: Discovery

#### Step 1.1: Find Code and Data

Locate the paper's code and supplementary materials:

```
Search for:
- Paper URL or DOI → check for GitHub link (often in a footnote or data availability section)
- "github.com [paper title]" via web_search
- "github.com [first author name] [paper keyword]" via web_search
- Papers With Code: web_fetch "https://paperswithcode.com/search?q={paper title}"
- Check Zenodo / Figshare / Hugging Face for datasets
```

If no public code or data is found, report:
```
## Reproducibility Audit: [Paper Title]

**Result: NOT ASSESSABLE** — No public code or data found.

Recommendations:
- Contact authors directly for code access
- Check if the venue has an artifact evaluation badge
- Look for later papers that replicate the method
```
Stop here if no code found.

#### Step 1.2: Clone Repository

Once a GitHub repo URL is found, clone it in the sandbox:

```bash
cd /home/user
git clone --depth 1 <repo_url> paper_code
cd paper_code
```

Record: `repo_url`, `commit_hash` (via `git rev-parse HEAD`), `repo_size`, `last_commit_date`.

#### Step 1.3: Inventory Assessment

Inspect the repository structure:

```bash
# List top-level files
ls -la

# Check for dependency specifications
find . -maxdepth 2 -name "requirements.txt" -o -name "pyproject.toml" -o -name "setup.py" -o -name "environment.yml" -o -name "Dockerfile" -o -name "Pipfile" -o -name "poetry.lock" -o -name "Cargo.toml" -o -name "package.json" -o -name "CMakeLists.txt"
```

Extract and record:
- Language(s) used
- Dependency count
- Presence of README with setup instructions
- Presence of test suite

### Phase 2: Environment Setup

#### Step 2.1: Build Environment

Based on the detected setup, attempt to build the environment:

**Python projects:**
```bash
python -m venv /home/user/venv
source /home/user/venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt 2>&1 | tail -30
```

**Conda environments:**
```bash
conda env create -f environment.yml 2>&1 || mamba env create -f environment.yml 2>&1
```

**Docker-based:**
```bash
docker build -t paper_repro . 2>&1
```

**Node.js projects:**
```bash
npm install 2>&1 | tail -30
```

Record:
- Setup success/failure
- Any missing or conflicting dependencies
- Time taken to build

#### Step 2.2: Setup Difficulty Score

Rate the setup difficulty:

| Score | Description |
|-------|-------------|
| **Trivial** | Single `pip install` succeeds cleanly |
| **Easy** | Minor version pin adjustments needed |
| **Moderate** | Several deprecated packages, manual fixes required |
| **Difficult** | Significant dependency conflicts, non-trivial fixes |
| **Impossible** | Cannot build in sandbox (proprietary data, hardware requirements, bit-rot) |

### Phase 3: Execution

#### Step 3.1: Identify Entry Points

Find how to run the paper's experiments:

```bash
# Check README for run instructions
cat README.md | grep -E "(run|train|evaluate|python|bash|\.sh)" -i | head -20

# Find run scripts
find . -name "run*.sh" -o -name "train*.py" -o -name "eval*.py" -o -name "main*.py" -o -name "experiment*.py" | head -10

# Check Makefile for targets
head -30 Makefile 2>/dev/null
```

#### Step 3.2: Attempt Minimal Reproduction

Run the most lightweight reproduction possible:

1. First try: run any quick test or demo that should work
2. Second try: run a single experiment with minimal data/config
3. Third try: run the full evaluation pipeline (if feasible)

For each attempt, capture:
- Command executed
- Exit code
- Runtime
- Output (stdout/stderr, truncated to 5000 chars)
- Key metric values from output

**CRITICAL:** Set a timeout of 15 minutes per run. If an experiment takes longer, mark it as "exceeded sandbox time limit" rather than failing.

#### Step 3.3: Compare Against Paper Claims

For each metric found in the execution output, compare against the paper's reported values:

| Paper Claim | Observed Value | Match? | Tolerance | Notes |
|---|---|---|---|---|
| Accuracy: 94.2% | 93.8% | ✓ Close | ±1% | Within expected variance |
| BLEU: 32.5 | 28.1 | ✗ Failed | ±2 | Significant gap |
| Training time: 2h | 2h15m | ✓ Close | ±20% | Hardware difference |

Use the `python` tool to parse and diff the results.

### Phase 4: Report Generation

Generate a structured reproducibility report:

```markdown
# Reproducibility Audit Report: [Paper Title]

## Overall Rating

| Dimension | Score (1-5) | Notes |
|---|---|---|
| **Code Availability** | X/5 | ... |
| **Setup Ease** | X/5 | ... |
| **Documentation Quality** | X/5 | ... |
| **Result Fidelity** | X/5 | ... |
| **Data Availability** | X/5 | ... |

**Overall Reproducibility Score: X.X / 5.0**

**Reproducibility Level:**
- [ ] **Gold**: All results fully reproduced within tolerance
- [ ] **Silver**: Major results reproduced, minor discrepancies
- [ ] **Bronze**: Partial reproduction, some results match
- [ ] **Not Reproducible**: Cannot reproduce claimed results
- [ ] **Not Assessable**: No public code/data

## Code Inventory
- Repository: [URL]
- Commit: [hash]
- Language: [Python/...]
- Dependencies: [N packages]
- Setup time: [X min]

## Setup Experience
[Detailed narrative of environment setup]

## Execution Results
[Per-experiment results with paper-vs-observed comparisons]

## Discrepancies Analysis
[Analysis of any differences between paper claims and observed results]

## Recommendations
- [Specific, actionable suggestions for improving reproducibility]
```

Save the report as Markdown and present it alongside the paper review.

## Integration with academic-paper-review

When used together with `academic-paper-review`, the reproducibility audit provides concrete evidence for the "Reproducibility" criterion in the methodology assessment table. Cross-reference:

- Low Setup Ease → evidence for reproducibility concerns
- Result Fidelity below 4 → suggests inflated or cherry-picked results
- Missing code → downgrade contribution significance (unverifiable claims)

## Quality Checklist

- [ ] Code repository located and cloned successfully
- [ ] Commit hash recorded for exact version reference
- [ ] Environment setup attempted with full error capture
- [ ] At least oneminimal reproduction attempted
- [ ] Paper claims extracted and matched against observed outputs
- [ ] Reproducibility level assigned with clear justification
- [ ] Discrepancies explained (hardware differences, missing hyperparameters, etc.)
- [ ] Report saved (ensure `mkdir -p /mnt/user-data/outputs`) to `/mnt/user-data/outputs/reproducibility-{paper-slug}.md`
