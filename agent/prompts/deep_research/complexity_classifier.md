# Complexity Classifier Prompt

Analyze the research brief below and classify its complexity.

<Research Brief>
{research_brief}
</Research Brief>

Classification criteria:

**simple**: Factual lookup, single-source answer, or list generation.
  Examples: "What is the capital of France?", "List 5 best restaurants in Tokyo"

**standard**: Requires multiple sources, analysis, or comparison of 2-3 items.
  Examples: "Compare React vs Vue for enterprise apps", "Summarize the latest developments in CRISPR"

**deep**: Multi-dimensional analysis, requires academic sources, comparison of many items,
  or comprehensive coverage across multiple domains.
  Examples: "Comprehensive analysis of AI safety approaches across OpenAI, Anthropic, and DeepMind",
  "Systematic literature review of transformer architecture variants"

Respond with:
- complexity: "simple", "standard", or "deep"
- estimated_depth: 1 (surface), 2 (moderate), or 3 (thorough)
- estimated_breadth: number of parallel search directions (1-6)
- reasoning: brief explanation for your classification
