---
name: vision-enrich
description: Guides autonomous decisions on when extracting and analyzing web images adds sufficient value to justify the token cost for visually dense research domains.
version: "1.0.0"
tags: [research, multimodal, vision]
---

# Vision Enrich Skill

## Overview

This skill provides the decision framework for autonomous visual data enrichment during deep research. It teaches the LLM **when** to extract and analyze images from web pages, **how** to evaluate the cost/benefit of visual data, and **what** constitutes high-value visual evidence.

The skill does NOT mandate image extraction — it provides heuristics so the LLM can make an informed autonomous decision per research context.

## When to Use This Skill

**Always load this skill when the research topic falls into these domains:**

### High-Value Visual Domains
| Domain | Why Visuals Matter | Example Triggers |
|--------|-------------------|------------------|
| **Financial Analysis** | Charts contain revenue/earnings data impossible to describe in text | "analyze NVIDIA quarterly earnings", "compare cloud provider growth" |
| **Data & Statistics** | Distributions, trends, and correlations are best understood visually | "COVID mortality trends", "global temperature change data" |
| **Architecture & Systems** | System diagrams convey structure more efficiently than paragraphs | "microservices architecture", "neural network architecture" |
| **Scientific Research** | Figures, microscopy, and experimental results are core evidence | "CRISPR mechanism", "protein folding structure" |
| **Product & UI Analysis** | Screenshots reveal design patterns and UX decisions | "compare landing page designs", "SaaS dashboard analysis" |
| **Geographic & Spatial** | Maps and spatial data require visual representation | "supply chain routes", "demographic heat maps" |
| **Engineering & CAD** | Blueprints and schematics are the primary information carrier | "circuit diagram", "mechanical assembly drawing" |

### When NOT to Use Visual Enrichment
- Pure text analysis (literature review, philosophy, legal analysis)
- Simple factual queries ("What is the capital of France?")
- Topics where images are likely decorative (lifestyle blogs, generic news)
- Research with very tight token budgets

## Core Principle

**Vision tokens are 10-100x more expensive than text tokens. Use them only when images carry information that cannot be adequately conveyed in text.**

A financial chart showing revenue growth from $10B to $60B over 5 years is worth the tokens. A stock photo of a smiling executive at a conference is not.

## Decision Framework

### Step 1: Assess Visual Information Density

Before calling `extract_web_images`, ask yourself:

```
1. Does this page likely contain DATA-HEAVY images (charts, diagrams, tables)?
   → Look for signals in: page title, URL patterns (/chart/, /dashboard/, /report/),
     search snippet mentions of "chart", "figure", "graph", "table"

2. Would seeing the image change my understanding or just confirm the text?
   → "Revenue grew 45% YoY" — the text tells you the number
   → "Revenue grew 45% YoY but the growth rate is decelerating" — the CHART
      shows the trend shape that text cannot fully capture

3. Can I extract the key insight from text alone, or is the visual pattern critical?
   → Text: "The architecture has 5 services"
   → Visual: The ARCHITECTURE DIAGRAM shows data flow direction, bottlenecks
```

### Step 2: Estimate Token Budget

```
extract_web_images cost model:
  - Fetch HTML: ~0 tokens (HTTP, not LLM)
  - Download images: ~0 tokens (HTTP, not LLM)  
  - Encode images: ~1K-150K tokens per image (depends on dimensions)
  - LLM vision analysis: ~same as encoding cost
  - Total per image: ~2K-300K tokens

Decision threshold:
  - Simple topic (2-3 search calls budget): SKIP image extraction
  - Standard topic (5-6 search calls budget): Extract 1-2 images IF high visual density
  - Deep topic (unlimited): Extract 2-3 images when domain matches high-value list
```

### Step 3: Execute or Skip

**EXTRACT when:**
- Domain is in the High-Value Visual Domains table above
- Search snippet or URL suggests data-heavy content
- The visual pattern (trend shape, distribution, structure) is the information
- Research depth is "standard" or "deep"

**SKIP when:**
- Text already captures all relevant information
- Images are likely decorative (stock photos, banners)
- Research depth is "simple" (limited token budget)
- Page is primarily text with no data visualization signals

### Step 4: Post-Extraction Analysis

After images are extracted and injected into context:

1. **Describe what you see**: Extract specific numbers, trends, patterns
2. **Cross-reference with text**: Does the visual confirm or contradict the textual claims?
3. **Cite the source**: Include the image URL and page URL in findings
4. **Note limitations**: If the image is partial, low-resolution, or ambiguous, state this

## Available Tools

When this skill is active and `vision_enrich_data` is enabled:

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `extract_web_images` | Extract images from web page HTML | After finding a promising URL in search results |
| `view_image` | View a local image file | When user uploads or references an image file |

## Workflow Example

### Financial Analysis Example

```
User: "Analyze NVIDIA's Q3 2026 earnings"

Research flow:
1. Search: "NVIDIA Q3 2026 earnings results" → finds results
2. Search result analysis:
   - investopedia.com/nvidia-earnings → likely has revenue charts
   → DECISION: EXTRACT images (high visual density domain: financial)
3. extract_web_images("https://investopedia.com/nvidia-earnings", max_images=2)
   → Extracts: revenue bar chart, segment breakdown pie chart
4. LLM sees images + text together
   → Analysis: "Revenue reached $35.1B (chart shows 94% YoY growth,
     accelerating from 78% in Q2). Data center segment is 87% of total
     (pie chart) — up from 80% last quarter."
5. Final compressed research includes visual + textual analysis
```

### Architecture Research Example

```
User: "Explain the Transformer architecture"

Research flow:
1. Search: "Transformer architecture diagram" → finds results
2. Search result analysis: arxiv paper page or blog with architecture diagram
   → DECISION: EXTRACT (domain: architecture/system design)
3. extract_web_images(url, max_images=1)
   → Extracts: encoder-decoder architecture diagram
4. LLM sees diagram + text → describes the visual flow
5. Findings include both textual explanation and diagram-based description
```

## Quality Checklist

Before finalizing visual-enriched research:

- [ ] Was image extraction used only for high-visual-density domains?
- [ ] Did the images provide information NOT available in text?
- [ ] Were specific numbers/patterns/trends extracted from the visuals?
- [ ] Were image sources properly cited?
- [ ] Was the token cost justified by the additional insights gained?
- [ ] Did I note any limitations (low resolution, partial data, outdated chart)?

## Common Mistakes to Avoid

- ❌ Extracting images from every search result page indiscriminately
- ❌ Calling extract_web_images for decorative or stock photos
- ❌ Describing images vaguely ("there is a chart showing growth") — extract specific data
- ❌ Ignoring images after extraction — analyze them thoroughly
- ❌ Using visual enrichment for simple factual queries
- ❌ Exceeding reasonable token budget (3+ images when 1-2 would suffice)

## Configuration

This skill's tools are controlled by SoulSearcher configuration flags:

```bash
# Enable basic vision (user images + view_image tool)
SOULSEARCHER_MODE=deepresearch
supports_vision=true

# Enable autonomous web image extraction (THIS SKILL's domain)
VISION_ENRICH_DATA=true
```

When `VISION_ENRICH_DATA=false`, the `extract_web_images` tool is not available, and this skill functions as a passive guide for manual visual analysis only.
