# Final Report HTML Prompt

Based on all the research conducted, create a comprehensive, visually rich HTML research report.

<Research Brief>
{research_brief}
</Research Brief>

For context, here are the user's messages:
<Messages>
{messages}
</Messages>

CRITICAL: Write the answer in the SAME language as the user's messages.
If the user wrote in Chinese, write in Chinese. If in English, write in English.

Today's date is {date}.

Here are the findings from the research:
<Findings>
{findings}
</Findings>

Use only this current-run evidence table for numbered citations:
{citation_table}

<Output Format>
Generate a COMPLETE, STANDALONE HTML document fragment. The output will be
wrapped in a proper HTML page by the platform — you ONLY need to output the
<style> block followed by the <article> content.

CRITICAL: Your entire output must be valid HTML. Do NOT output markdown.
Do NOT output explanations outside the HTML.

Structure:
```
<style>
/* Base CSS for professional research report styling */
</style>

<article class="research-report">

  <header class="report-header">
    <h1>Report Title</h1>
    <div class="report-meta">
      <span class="date">Generated: {date}</span>
    </div>
  </header>

  <section class="executive-summary">
    <h2>Executive Summary</h2>
    <div class="callout callout-summary">
      <p>Key findings summary...</p>
    </div>
  </section>

  ... sections ...

  <section class="sources">
    <h2>Sources</h2>
    <ol>
      <li id="src-N"><a href="URL">Title</a> — description</li>
    </ol>
  </section>

</article>
```
</Output Format>

<CSS Requirements>
Include a <style> block at the very beginning with CSS for:
- .research-report — max-width 860px, system font stack, line-height 1.75
- h1/h2/h3 — sized with bottom borders for visual hierarchy
- .callout — colored left-border boxes (summary=blue, warning=amber, insight=green)
- figure — centered, responsive images with rounded corners and shadow
- figcaption — italic, gray, smaller text
- table — full-width, striped rows, highlight cells for key data
- .sources — top-border separator, smaller text
- Responsive: @media (max-width: 640px) adjustments
</CSS Requirements>

<Content Guidelines>
1. Use semantic HTML5: <article>, <section>, <header>, <figure>, <figcaption>
2. Use <div class="callout callout-summary"> for executive summary
3. Use <div class="callout callout-insight"> for key analytical insights
4. Use <div class="callout callout-warning"> for limitations or caveats
5. For data tables: wrap in <div class="table-wrapper">, use <thead>/<tbody>
6. Highlight key cells with class="highlight", "positive", or "negative"
7. For images: use <!-- IMAGE: source_key --> placeholder comments
8. For charts that would benefit from visualization: use <!-- CHART: description -->
9. Citations: use <sup><a href="#src-N">[N]</a></sup> inline, with
   <li id="src-N"> in the sources section
</Content Guidelines>

{skill_writing_context}
<Image & Chart Rules>
- If research involved charts/graphs/diagrams, add <!-- IMAGE: key --> placeholders
  where images should appear. The platform will replace these with actual <img> tags.
- Be specific about WHERE images should go and WHAT they should show
- Each placeholder should be on its own line
- Example: <!-- IMAGE: https://example.com/revenue-chart.png -->
- For data that would benefit from visual charts (trends, comparisons, distributions),
  add <!-- CHART: description of what the chart should show -->
</Image & Chart Rules>

<Report Structure>
Choose the most appropriate structure:
- Academic: Abstract → Introduction → Literature Review → Analysis → Conclusion
- Market Research: Executive Summary → Market Overview → Competitive Landscape → Opportunities → Recommendations
- Comparison: Overview → Option A → Option B → [Option C...] → Comparison Table → Recommendation
- Deep Analysis: Executive Summary → Introduction → Findings (multiple sections) → Cross-Cutting Analysis → Limitations → Conclusion
- Summary: Overview → Background → Current State → Key Players → Future Outlook

For each section, use <section> tags with proper <h2> headings.
</Report Structure>

<Citation Rules>
- Use only citation numbers that appear in the Evidence Citation Table
- Do not cite memory context, prior runs, uncached notes, or URLs that are not in the table
- Assign each unique URL a citation number
- Inline: <sup><a href="#src-N">[N]</a></sup>
- Sources section: <li id="src-N"><a href="URL">Title</a></li>
- Number sequentially: [1], [2], [3]...
- If a claim is not supported by the table, omit it or mark it as a limitation
- Citations are extremely important — users rely on them for verification
</Citation Rules>

CRITICAL REMINDERS:
- Output ONLY the HTML fragment (style + article). No markdown, no explanations.
- The <style> block MUST be the first thing in your output.
- All content inside <article class="research-report">.
- Use semantic HTML5. No <div> soup.
- Include image placeholders when research involved visual data.
