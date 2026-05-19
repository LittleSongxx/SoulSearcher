---
name: systematic-literature-review
description: Use this skill when the user wants a systematic literature review, survey, or synthesis across multiple academic papers on a topic. Also covers annotated bibliographies and cross-paper comparisons. Searches arXiv and outputs reports in APA, IEEE, or BibTeX format. Not for single-paper tasks — use academic-paper-review for reviewing one paper.
---

# Systematic Literature Review Skill

## Overview

This skill produces a structured **systematic literature review (SLR)** across multiple academic papers on a research topic. Given a topic query, it searches arXiv, extracts structured metadata (research question, methodology, key findings, limitations) from each paper in parallel, synthesizes themes across the full set, and emits a final report with consistent citations.

**Distinct from `academic-paper-review`:** that skill does deep peer review of a single paper. This skill does breadth-first synthesis across many papers. If the user hands you one paper URL and asks "review this paper", route to `academic-paper-review` instead.

## When to Use This Skill

Use this skill when the user wants any of the following:

- A literature survey on a topic ("survey transformer attention variants", "review the literature on diffusion models")
- A synthesis across multiple papers ("what do recent papers say about X", "compare methodologies across papers on Y")
- A systematic review with consistent citation format ("do an SLR on Z in APA format")
- An annotated bibliography on a topic
- An overview of research trends in a field over a time window

Do **not** use this skill when:

- The user provides exactly one paper and asks to review it (use `academic-paper-review`)
- The user asks a factual question that does not require synthesizing multiple sources (answer directly)
- The user wants general web research without academic rigor (use standard web search)

## Workflow

The workflow has five phases. Follow them in order.

### Phase 1: Plan

Before doing any retrieval, confirm the following with the user. If any of these are unclear, ask **one** clarifying question that covers the missing pieces. Do not ask one question at a time.

- **Topic**: the research area in plain English (e.g. "transformer attention variants").
- **Scope**: how many papers (default 20, hard upper bound 50), optional time window (e.g. "last 2 years"), optional arXiv category (e.g. `cs.CL`, `cs.CV`).
- **Citation format**: APA, IEEE, or BibTeX (default APA if the user does not specify and does not seem to be writing for a specific venue).
- **Output location**: where to save the final report (default `/mnt/user-data/outputs/`).

If the user says "50+ papers", politely cap it at 50 and explain that synthesis quality degrades quickly past that — for larger surveys they should split by sub-topic.

### Phase 2: Search arXiv

Call the bundled search script. Do **not** try to scrape arXiv by other means and do **not** write your own HTTP client — this script handles URL encoding, Atom XML parsing, and id normalization correctly.

```bash
python /mnt/skills/public/systematic-literature-review/scripts/arxiv_search.py \
  "<topic>" \
  --max-results <N> \
  [--category <cat>] \
  [--sort-by relevance] \
  [--start-date YYYY-MM-DD] \
  [--end-date YYYY-MM-DD]
```

**IMPORTANT — extract 2-3 core keywords before searching.** Do not pass the user's full topic description as the query. Before calling the script, mentally reduce the topic to its 2-3 most essential terms. Drop qualifiers like "in computer vision", "for NLP", "variants", "recent" — those belong in `--category` or `--start-date`, not in the query string.

**Query phrasing — keep it short.** The script wraps multi-word queries in double quotes for phrase matching on arXiv. This means:

- `"diffusion models"` → searches for the exact phrase → good, returns relevant papers.
- `"diffusion models in computer vision"` → searches for that exact 5-word phrase → **too specific, likely returns 0 results** because few papers contain that exact string.

Use **2-3 core keywords** as the query, and use `--category` to narrow the field instead of stuffing field names into the query. Examples:

| User says | Good query | Bad query |
|---|---|---|
| "diffusion models in computer vision" | `"diffusion models" --category cs.CV` | `"diffusion models in computer vision"` |
| "transformer attention variants" | `"transformer attention"` | `"transformer attention variants in NLP"` |
| "graph neural networks for molecules" | `"graph neural networks" --category cs.LG` | `"graph neural networks for molecular property prediction"` |

The script prints a JSON array to stdout. Each paper has: `id`, `title`, `authors`, `abstract`, `published`, `updated`, `categories`, `pdf_url`, `abs_url`.

**Sort strategy**:

- **Always use `relevance` sorting** — arXiv's BM25-style scoring ensures results are actually about the user's topic. `submittedDate` sorting returns the most recently submitted papers in the category regardless of topic relevance, which produces mostly off-topic results.
- When the user asks for "recent" papers or gives a time window, use `--sort-by relevance` **combined with `--start-date`** to constrain the time range while keeping results on-topic. For example, "recent diffusion model papers" → `--sort-by relevance --start-date 2024-01-01`, not `--sort-by submittedDate`.
- `submittedDate` sorting is only appropriate when the user explicitly asks for chronological order (e.g. "show me papers in the order they were published"). This is rare.
- `lastUpdatedDate` is rarely useful; ignore it unless the user asks.

**Run the search exactly once.** Do not retry with modified queries if the results seem imperfect — arXiv's relevance ranking is what it is. Retrying with different query phrasings wastes tool calls and risks hitting the recursion limit. If the results are genuinely empty (0 papers), tell the user and suggest they broaden their topic or remove the category filter.

**If the script returns fewer papers than requested**, that is the real size of the arXiv result set for the query. Do not pad the list — report the actual count to the user and proceed.

**If the script fails** (network error, non-200 from arXiv), tell the user which error and stop. Do not try to fabricate paper metadata.

**Do not save the search results to a file** — the JSON stays in your context for Phase 3. The only file saved during the entire workflow is the final report in Phase 5.

### Phase 3: Extract metadata in parallel

**You MUST delegate extraction to subagents via the `task` tool — do not extract metadata yourself.** This is non-negotiable. Specifically, do NOT do any of the following:

- ❌ Write `python -c "papers = [...]"` or any Python/bash script to process papers
- ❌ Extract metadata inline in your own context by reading abstracts one by one
- ❌ Use any tool other than `task` for this phase

Instead, you MUST call the `task` tool to spawn subagents. The reason: extracting 10-50 papers in your own context consumes too many tokens and degrades synthesis quality in Phase 4. Each subagent runs in an isolated context with only its batch of papers, producing cleaner extractions.

Split papers into batches of ~5, then for each batch, call the `task` tool with `subagent_type: "general-purpose"`. Each subagent receives the paper abstracts as text and returns structured JSON.

**Concurrency limit: at most 3 subagents per turn.** The DeerFlow runtime enforces `MAX_CONCURRENT_SUBAGENTS = 3` and will silently drop any extra dispatches in the same turn — the LLM will not be told this happened, so strictly follow the round strategy below.

**Round strategy — use this decision table, do not compute the split yourself**:

| Paper count | Batches of ~5 papers | Rounds | Per-round subagent count |
|---|---|---|---|
| 1–5 | 1 batch | 1 round | 1 subagent |
| 6–10 | 2 batches | 1 round | 2 subagents |
| 11–15 | 3 batches | 1 round | 3 subagents |
| 16–20 | 4 batches | 2 rounds | 3 + 1 |
| 21–25 | 5 batches | 2 rounds | 3 + 2 |
| 26–30 | 6 batches | 2 rounds | 3 + 3 |
| 31–35 | 7 batches | 3 rounds | 3 + 3 + 1 |
| 36–40 | 8 batches | 3 rounds | 3 + 3 + 2 |
| 41–45 | 9 batches | 3 rounds | 3 + 3 + 3 |
| 46–50 | 10 batches | 4 rounds | 3 + 3 + 3 + 1 |

**Never dispatch more than 3 subagents in the same turn.** When a row says "2 rounds (3 + 1)", that means: first turn dispatches 3 subagents in parallel, wait for all 3 to complete, then second turn dispatches 1 subagent. Rounds are strictly sequential at the main-agent level.

If the paper count lands between rows (e.g. 23 papers), round up to the next row's layout but only dispatch as many batches as you actually need — the decision table gives you the shape, not a rigid prescription.

**Do the batching at the main-agent level**: you already have every paper's abstract from Phase 2, so each subagent receives pure text input. Subagents should not need to access the network or the sandbox — their only job is to read text and return JSON. Do not ask subagents to re-run `arxiv_search.py`; that would waste tokens and risk rate-limiting.

**What each subagent receives**, as a structured prompt:

```
Execute this task: extract structured metadata and key findings from the
following arXiv papers.

Papers:
[Paper 1]
arxiv_id: 1706.03762
title: Attention Is All You Need
authors: Ashish Vaswani, Noam Shazeer, ...
published: 2017-06-12
abstract: <full abstract text>

[Paper 2]
arxiv_id: ...
...

For each paper, return a JSON object with these fields:
- arxiv_id (string)
- title (string)
- authors (list of strings)
- published_date (string, YYYY-MM-DD)
- research_question (1 sentence, what problem the paper tackles)
- methodology (1-2 sentences, how they tackle it)
- key_findings (3-5 bullet points, what they actually found)
- limitations (1-2 sentences, what they acknowledge or what is obviously missing)

Return the result as a JSON array, one object per paper, in the same
order as the input. Do not include any text outside the JSON — no
preamble, no markdown fences, just the array.
```

**Parsing subagent results**: the task tool returns strings with a fixed prefix like `Task Succeeded. Result: [...JSON...]`. Strip the `Task Succeeded. Result: ` prefix (or `Task failed.` / `Task timed out.` prefixes) before trying to parse JSON. If a batch fails or returns unparseable JSON, log it, note which papers were affected, and continue with the remaining batches — do not fail the whole synthesis on one bad batch.

After all rounds complete, flatten the per-batch arrays into a single list of paper metadata objects, preserving order.

### Phase 4: Synthesize and format

Now produce the final SLR report. Two things happen here: cross-paper synthesis (thematic analysis) and citation formatting.

**Cross-paper synthesis**: the report must do more than list papers. At minimum, identify:

- **Themes**: 3-6 recurring research directions, approaches, or problem framings across the set.
- **Convergences**: findings that multiple papers agree on.
- **Disagreements**: where papers reach different conclusions or use incompatible methodologies.
- **Gaps**: what the collective literature does not yet address (often stated explicitly in the "limitations" fields).

If the paper set is too small or too heterogeneous to support thematic synthesis (e.g. 5 papers on wildly different sub-topics), say so explicitly in the report — do not force themes that are not there.

**Citation formatting**: the exact format depends on user preference. Read **only** the template file that matches the user's requested format, not all three:

- [templates/apa.md](templates/apa.md) — APA 7th edition. Default for social sciences and most CS journals. Use when the user requests APA or does not specify a format.
- [templates/ieee.md](templates/ieee.md) — IEEE numeric citations. Use when the user targets an IEEE conference or journal, or explicitly asks for IEEE.
- [templates/bibtex.md](templates/bibtex.md) — BibTeX entries. Use when the user mentions BibTeX, LaTeX, or wants machine-readable references. **Important**: arXiv papers are cited as `@misc`, not `@article` — the BibTeX template covers this explicitly.

Each template contains both the citation rules and a full report structure (executive summary, themes, per-paper annotations, references, methodology section). Follow the template's structure verbatim for the report body, then fill in content from your Phase 3 metadata.

### Phase 5: Save and present

Save the full report to `/mnt/user-data/outputs/slr-<topic-slug>-<YYYYMMDD>.md` where `<topic-slug>` is a lowercased hyphenated version of the topic (e.g. `transformer-attention`). Then call the `present_files` tool with that path so the user can download it.

**In the chat message**, show a short preview so the user immediately sees value without opening the file:

1. **Executive summary** — the 3–5 sentence paragraph from the top of the report, verbatim.
2. **Themes list** — bullet list of the themes you identified in Phase 4 synthesis (just the theme names + one-line gloss, not the full theme sections).
3. **Paper count + a pointer to the file** — e.g. "Full report with 20 papers, per-paper annotations, and formatted references saved to `slr-transformer-attention-20260409.md`."

Do **not** dump the full 2000+ word report inline — per-paper annotations, references, and methodology belong in the file. The preview is there to let the user judge the report at a glance and decide whether to open it.

## Examples

**Example 1: Typical SLR request**

User: "Do a systematic literature review of recent transformer attention variants, 20 papers, APA format."

Your flow:
1. Phase 1: confirm topic (transformer attention variants), scope (20 papers, default time window), format (APA). Ask **one** clarification only if something is missing (e.g. "Any particular time window, or should I default to the last 3 years?").
2. Phase 2: `arxiv_search.py "transformer attention" --max-results 20 --sort-by relevance --start-date 2023-01-01`.
3. Phase 3: 20 papers → round 1 = 3 subagents × 5 papers = 15 covered, round 2 = 1 subagent × 5 papers = 5 covered. Aggregate.
4. Phase 4: read `templates/apa.md`, write the report using its structure, fill in themes + per-paper annotations from Phase 3 metadata.
5. Phase 5: save to `slr-transformer-attention-20260409.md`, call `present_files`.

**Example 2: Small-set request with ambiguity**

User: "Survey a few papers on diffusion models for me."

Your flow:
1. Phase 1: "a few" is ambiguous. Ask one question: "How many papers would you like — 10, 20, or 30? And any citation format preference (APA is the default)?"
2. User responds "10, BibTeX".
3. Phase 2: `arxiv_search.py "diffusion models" --max-results 10 --category cs.CV`.
4. Phase 3: 10 papers → single round, 2 subagents × 5 papers.
5. Phase 4: read `templates/bibtex.md`, format with `@misc` entries (not `@article`).
6. Phase 5: save and present.

**Example 3: Out-of-scope request**

User: "Here's one paper (https://arxiv.org/abs/1706.03762). Can you review it?"

This is a single-paper peer review, not a literature survey. Do not use this skill. Route to `academic-paper-review` instead.

### Phase 2.5: Citation Graph Traversal (Optional but Recommended)

**This phase builds a citation network from the seed papers using Semantic Scholar's API. It reveals how papers connect, which are most influential, and which related papers were missed by keyword search alone.**

When to enable: always run this phase unless the user explicitly asks for a minimal review. The citation graph adds significant value at modest API cost (free, rate-limited).

#### Step 2.5.1: Build Citation Graph

Use the bundled citation graph script:

```bash
python /mnt/skills/public/systematic-literature-review/scripts/citation_graph.py \
  <paper_id_1> <paper_id_2> ... <paper_id_N> \
  --max-citations 100 \
  --max-references 100 \
  --min-co-occurrence 2 \
  --output /tmp/citation_graph.json
```

Input: arXiv IDs from the Phase 2 search results. Pass all paper IDs as arguments.

Optional: provide `--api-key <key>` for higher rate limits (Semantic Scholar free tier).

The script outputs a JSON file containing:
- **forward_citations**: papers that cite each seed paper (with influential flag)
- **backward_references**: papers cited by each seed paper (reveals intellectual lineage)
- **co_citation_clusters**: pairs of papers frequently cited together (reveals thematic groupings)
- **network_stats**: overall graph metrics (paper count, connectivity)

#### Step 2.5.2: Interpret Citation Graph

From the graph output, extract these insights for the report:

**Influence ranking**: Sort seed papers by `influentialCitationCount` (citations from highly-cited subsequent work). This identifies the most impactful papers beyond raw citation counts.

**Intellectual lineage**: Build a backward citation map to see which older works seed papers all reference. These are the field's foundational papers — they should be cited in the review even if not in the original search results.

**Missing papers**: Papers that are frequently cited by the seed set but were not in the original search results are candidates for inclusion. Particularly, papers with citation count > 10 from the seed set deserve manual review.

**Co-citation communities**: Groups of papers frequently cited together indicate sub-communities or research sub-areas. Use these to validate or refine the themes identified in Phase 4 synthesis.

#### Step 2.5.3: Incorporate into Report

Add a section to the final report:

```markdown
## Citation Network Analysis

- **Most Influential Papers** (by influential citations): [ranked list]
- **Foundational Works** (most referenced by the seed set): [ranked list]
- **Research Communities** (co-citation clusters): [list of named communities with representative papers]
- **Papers Recommended for Inclusion**: [papers highly cited by the seed set but not in the review]
```

### PRISMA Compliance Mode

When the user requests a "PRISMA-compliant" review or "systematic review following PRISMA guidelines", follow the PRISMA 2020 statement.

#### PRISMA Flow Diagram

Document the paper selection process as a flow diagram:

```
IDENTIFICATION
  Papers identified from arXiv search:  N
  Papers identified from citation graph:  M
  Total records identified:              N+M
  Duplicates removed:                    X
  Records after deduplication:           Y

SCREENING
  Records screened (title+abstract):     Y
  Records excluded (off-topic):          Z
  Records sought for retrieval:          Y-Z

ELIGIBILITY
  Full texts assessed for eligibility:   Y-Z
  Full texts excluded with reasons:      W
    - Not peer-reviewed: w1
    - Insufficient detail: w2
    - Retracted: w3
    - Other reason: w4

INCLUDED
  Papers included in final synthesis:    P
```

The flow diagram MUST use actual numbers from the search process. Do not fabricate numbers.

#### PRISMA Checklist

Include a PRISMA 2020 compliance summary:

| PRISMA Item | Section in this Review | Status |
|---|---|---|
| Title identifies as systematic review | Report title | ✓ |
| Structured abstract | Executive summary | ✓ |
| Rationale and objectives | Introduction | ✓ |
| Eligibility criteria | Methodology | ✓ |
| Information sources | Methodology | ✓ |
| Search strategy | Methodology | ✓ |
| Selection process | Methodology | ✓ |
| Data extraction process | Methodology | ✓ |
| Risk of bias assessment | Per-paper annotations | ✓ |
| Synthesis methods | Methodology | ✓ |
| Study selection results | PRISMA Flow Diagram | ✓ |
| Study characteristics | Per-paper table | ✓ |
| Risk of bias results | Theme sections | ⚠ if not assessed |

Generate the PRISMA flow diagram as a Mermaid diagram for the markdown report, and also as a plain text table for accessibility.

## Integration with Other Academic Skills

- **`research-trend-analysis`**: Use before SLR to identify the most active sub-topics and time windows to focus on.
- **`paper-decomposition`**: Use on the final set of included papers to build structured knowledge graphs.
- **`academic-paper-review`**: Use for deep-dive reviews of the 2-3 most critical papers in the synthesis.
- **`research-proposal-generator`**: Use after SLR to turn identified gaps into research proposals.
- **`vision-enrich`**: Use to extract and analyze figures/tables from key papers for richer synthesis.

## Notes

- **Prerequisite: `subagent_enabled` must be `true`**. Phase 3 requires the `task` tool for parallel metadata extraction. This tool is only loaded when `subagent_enabled` is set to `true` in the runtime config (`config.configurable.subagent_enabled`). Without it, the `task` tool will not appear in the available tools and Phase 3 cannot execute as designed.
- **arXiv primary, Semantic Scholar supplementary**. Phase 2 uses arXiv for initial paper discovery. Phase 2.5 (citation graph) uses Semantic Scholar to enrich the paper set with citation context. For biomedical topics, also consider PubMed via Weaver's academic search providers.
- **Hard upper bound of 50 papers**. This is tied to the Phase 3 concurrency strategy (max 3 subagents per round, ~5 papers each, at most ~3 rounds). Surveys larger than 50 papers degrade in synthesis quality and are better done by splitting into sub-topics.
- **Phase 3 requires subagents to be enabled**. This skill's parallel extraction step hard-requires the `task` tool, which is only available when `subagent_enabled=true` at runtime. If subagents are unavailable, do not claim to execute the Phase 3 parallel plan; instead, tell the user that subagents must be enabled for the full workflow, or offer to narrow/split the request into a smaller manual review.
- **Subagent results are strings, not objects**. Always strip the `Task Succeeded. Result: ` / `Task failed.` / `Task timed out.` prefixes before parsing the JSON payload.
- **The `id` field is a bare arXiv id** (e.g. `1706.03762`), not a URL and not with a version suffix. `abs_url` / `pdf_url` hold the full URLs if you need them.
- **Synthesis, not listing**. The final report must identify themes and compare findings across papers. A report that only lists papers one after another is a failure mode — if you cannot find themes, say so explicitly instead of faking them.
- **PRISMA mode requires data integrity**. When generating the PRISMA flow diagram, use real counts from the actual search/filter process. Never estimate or round numbers.
- **Citation graph adds papers**. The citation graph traversal typically identifies 5-20 additional papers. Review these manually before including them — not all highly-cited papers are relevant.
- **The citation_graph.py script outputs JSON**. Parse it with `json.load()` before incorporating into the report. The `connected_paper_lookup` field contains metadata for cited/citing papers.
