---
name: research-trend-analysis
description: Analyzes research trends across years by searching academic databases, building trend curves, identifying emerging topics, and generating visualizations.
version: "1.0.0"
tags: [academic, research, analytics]
---

# Research Trend Analysis Skill

## Overview

This skill performs quantitative analysis of research trends across academic publications. It searches multiple years of literature, tracks keyword and topic evolution, identifies emerging and declining research areas, builds co-occurrence networks, and generates trend visualizations.

## When to Use This Skill

**Use when:**

- User asks "what are the trends in [field]?", "how has [field] evolved?", "what's hot in [area]?"
- User wants a quantitative understanding of research activity over time
- User is deciding on a research direction and wants to assess field momentum
- User is writing a survey and needs trend data to contextualize
- User asks "is [topic] gaining or losing popularity?"

## Workflow

### Phase 1: Data Collection

#### Step 1.1: Multi-Year Search

Search across a sliding window of years to build temporal data:

```
For each year in [current_year-5 ... current_year]:
  Search arXiv: "[topic]" with --start-date YYYY-01-01 --end-date YYYY-12-31
  If domain is biomed: also search PubMed with same date range
  Record: paper count, average citation count (from Semantic Scholar)
```

Use the bundled script for arXiv searches:
```bash
python /mnt/skills/public/systematic-literature-review/scripts/arxiv_search.py \
  "<topic>" --max-results 50 --sort-by relevance --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

For broader reach, also use the Semantic Scholar API through SoulSearcher's academic search providers.

#### Step 1.2: Sub-Topic Mining

From the collected papers, extract sub-topics:

1. Collect all paper titles and abstracts
2. Extract key phrases (2-4 word n-grams)
3. Filter to domain-relevant terms
4. Group similar terms via embedding similarity or keyword overlap

### Phase 2: Trend Analysis

#### Step 2.1: Growth Rate Calculation

For each sub-topic, calculate year-over-year metrics:

| Metric | Formula | Interpretation |
|---|---|---|
| **Absolute Growth** | papers_year_N - papers_year_N-1 | Raw increase |
| **Growth Rate** | (papers_year_N / papers_year_N-1) - 1 | Relative change |
| **CAGR** | (papers_last / papers_first)^(1/years) - 1 | Compound annual growth |
| **Citation Velocity** | avg_cites_year_N / avg_cites_year_N-1 | Attention acceleration |
| **Momentum Score** | weighted combination of above | Overall trajectory |

#### Step 2.2: Emerging Topic Detection

Identify topics that are new and growing fast:

```
Emerging score = Growth_Rate × Recency_Weight × Citation_Velocity
where Recency_Weight assigns higher weight to more recent years
```

#### Step 2.3: Decline Detection

Identify topics with declining activity:

```
Decline score = negative_growth_rate × sustained_duration
Flag as "declining" if decline > 20% over 2+ consecutive years
```

### Phase 3: Network Analysis

#### Step 3.1: Keyword Co-occurrence Network

Build a network of keywords that frequently appear together:

1. Extract keywords from all papers
2. Count co-occurrence pairs (keywords appearing in same paper)
3. Filter to top N most frequent pairs
4. Identify clusters (communities) of related keywords

#### Step 3.2: Author and Institution Analysis

If the user is interested in "who" not just "what":

- Top authors by publication count and citation impact
- Top institutions by publication volume
- Author collaboration networks (co-authorship)
- Geographic distribution of research activity

### Phase 4: Visualization and Report

#### Step 4.1: Generate Trend Charts

Create visualizations using Python in sandbox:

```python
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

# 1. Paper count over time (line chart)
# 2. Sub-topic growth rates (bar chart, sorted by growth)
# 3. Emerging vs declining topics (scatter plot: growth rate vs volume)
# 4. Keyword co-occurrence heatmap
# 5. Research landscape map (bubble chart: x=growth, y=volume, size=citations)
```

Save all charts as PNG files.

#### Step 4.2: Generate Report

Produce a structured report:

```markdown
# Research Trend Analysis: [Topic / Field]

## Executive Summary
[2-3 paragraphs on key findings: top growing areas, declining areas, overall trajectory]

## Publication Volume Trends
[Chart: Papers per year, 5-year window]
[Analysis of overall growth/decline]

## Hot Topics (Rising ⬆)
| Topic | Growth Rate | Current Volume | Momentum Score |
|---|---|---|---|
| ... | +XX% | N papers | X.X |

## Cooling Topics (Declining ⬇)
| Topic | Decline Rate | Current Volume | Years Declining |
|---|---|---|---|
| ... | -XX% | N papers | X |

## Emerging Research Areas
[3-5 newly emerging areas with growth rate > 50% and recent first appearance]

## Keyword Co-occurrence Network
[Chart: network visualization or heatmap]
[Narrative: which topics cluster together, interdisciplinary connections]

## Key Players
- **Most Active Authors**: [top 5]
- **Leading Institutions**: [top 5]

## Predictions and Outlook
[Based on trajectory analysis, what trends are likely to continue? What's on the horizon?]

## Methodology Notes
- Data sources: arXiv, Semantic Scholar, [others used]
- Time window: YYYY-MM to YYYY-MM
- Total papers analyzed: N
- Caveats: arXiv coverage varies by field; preprints may not represent final publication venues
```

## Integration with systematic-literature-review

This skill complements `systematic-literature-review`:

- **SLR**: deep, qualitative synthesis across papers
- **Trend Analysis**: broad, quantitative mapping of research activity

When both are used together, the trend analysis provides the temporal context for the SLR's thematic synthesis.

## Quality Checklist

- [ ] At least 5 years of data collected
- [ ] Minimum 200 papers analyzed (or explanation if field is very small)
- [ ] Growth rates calculated for top 10+ sub-topics
- [ ] At least 3 visualizations generated and embedded
- [ ] Emerging and declining topics clearly identified with evidence
- [ ] Caveats about data source limitations disclosed
- [ ] Report saved (ensure `mkdir -p /mnt/user-data/outputs`) to `/mnt/user-data/outputs/trend-analysis-{topic-slug}.md`
