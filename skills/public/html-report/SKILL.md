---
name: html-report
description: Use this skill when generating the final research report in HTML format. This skill provides the semantic HTML5 structure, CSS styling rules, image/chart/table embedding patterns, and quality guidelines for producing professional, visually rich research reports instead of plain markdown. Trigger when report_format is "html" or when the user requests a visually enhanced report.
---

# HTML Report Skill

## Overview

This skill teaches how to generate professional, visually rich research reports using semantic HTML5 + inline CSS. It replaces plain markdown output with structured, styled content that supports embedded images, SVG charts, styled tables, callout boxes, and responsive layout.

## When to Use This Skill

- `report_format` is set to `"html"` in configuration
- User explicitly requests "HTML report" or "rich report format"
- Report would benefit from embedded images, charts, or styled data tables
- The research findings include visual data that markdown cannot adequately present

## Core Principle

**Content first, style second.** The HTML structure should be semantic and clean. CSS enhances readability but should never distract from the information. Every visual element (image, chart, styled table) must add information value — decoration has no place in a research report.

## HTML Document Structure

Generate a **complete, standalone HTML fragment** (no `<html>`, `<head>`, or `<body>` — the platform wraps these):

```html
<style>
/* CSS injected here — see CSS Template below */
</style>

<article class="research-report">

  <header class="report-header">
    <h1>Report Title</h1>
    <div class="report-meta">
      <span class="date">Generated: YYYY-MM-DD</span>
      <span class="classification">Type: Research Analysis</span>
    </div>
  </header>

  <section class="executive-summary">
    <h2>Executive Summary</h2>
    <!-- 2-3 paragraph overview with key findings -->
  </section>

  <!-- Multiple sections as needed -->
  <section>
    <h2>Section Title</h2>
    <!-- Content with images, tables, charts -->
  </section>

  <section class="sources">
    <h2>Sources</h2>
    <!-- Numbered citation list -->
  </section>

</article>
```

## CSS Template

Always include this base CSS in a `<style>` block at the start of the output:

```css
.research-report {
  max-width: 860px;
  margin: 0 auto;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
  line-height: 1.75;
  color: #1a1a1a;
  padding: 1em 0;
}
.research-report h1 {
  font-size: 2em;
  border-bottom: 3px solid #2563eb;
  padding-bottom: 0.3em;
  margin-bottom: 0.3em;
  color: #111827;
}
.research-report h2 {
  font-size: 1.5em;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 0.2em;
  margin-top: 1.5em;
  color: #1f2937;
}
.research-report h3 {
  font-size: 1.2em;
  margin-top: 1.2em;
  color: #374151;
}
.research-report p { margin: 0.8em 0; }
.research-report a { color: #2563eb; text-decoration: none; }
.research-report a:hover { text-decoration: underline; }
.research-report .report-header {
  margin-bottom: 1.5em;
  padding-bottom: 1em;
  border-bottom: 1px solid #e5e7eb;
}
.research-report .report-meta {
  font-size: 0.85em;
  color: #6b7280;
  display: flex; gap: 1.5em; flex-wrap: wrap;
}
.research-report .report-meta span { display: inline-block; }

/* Callout boxes */
.research-report .callout {
  padding: 1em 1.2em;
  margin: 1em 0;
  border-radius: 6px;
  border-left: 4px solid;
}
.research-report .callout-summary {
  background: #eff6ff;
  border-color: #2563eb;
}
.research-report .callout-warning {
  background: #fef3c7;
  border-color: #f59e0b;
}
.research-report .callout-insight {
  background: #f0fdf4;
  border-color: #16a34a;
}

/* Figures (images and charts) */
.research-report figure {
  margin: 1.5em 0;
  text-align: center;
}
.research-report figure img,
.research-report figure svg {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.research-report figcaption {
  font-size: 0.88em;
  color: #6b7280;
  margin-top: 0.5em;
  font-style: italic;
}

/* Tables */
.research-report .table-wrapper {
  overflow-x: auto;
  margin: 1.2em 0;
}
.research-report table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95em;
}
.research-report thead th {
  background: #f3f4f6;
  font-weight: 600;
  text-align: left;
  padding: 10px 14px;
  border-bottom: 2px solid #d1d5db;
  white-space: nowrap;
}
.research-report tbody td {
  padding: 9px 14px;
  border-bottom: 1px solid #e5e7eb;
  vertical-align: top;
}
.research-report tbody tr:hover { background: #f9fafb; }
.research-report td.highlight {
  background: #fef3c7;
  font-weight: 600;
}
.research-report td.positive { color: #16a34a; font-weight: 600; }
.research-report td.negative { color: #dc2626; font-weight: 600; }

/* Citations */
.research-report .citation { font-size: 0.85em; color: #6b7280; }
.research-report sup a {
  font-size: 0.75em;
  color: #2563eb;
  text-decoration: none;
  padding: 0 2px;
}
.research-report .sources {
  margin-top: 2em;
  padding-top: 1em;
  border-top: 2px solid #e5e7eb;
  font-size: 0.9em;
}
.research-report .sources ol { padding-left: 1.5em; }
.research-report .sources li { margin-bottom: 0.3em; }

/* Responsive */
@media (max-width: 640px) {
  .research-report { padding: 0.5em; }
  .research-report h1 { font-size: 1.5em; }
  .research-report table { font-size: 0.85em; }
}
```

## Image Embedding

### Web-sourced images (from extract_web_images)

Use the placeholder syntax — these will be replaced by the platform with actual base64 data:

```html
<!-- IMAGE: https://example.com/chart.png -->
```

The platform will replace this with:
```html
<figure>
  <img src="data:image/png;base64,..." alt="Chart from example.com">
  <figcaption>Source: example.com</figcaption>
</figure>
```

### SVG Charts (from chart-visualization skill)

Inline SVG can be embedded directly in the HTML. When research data calls for charts:

1. Identify the data that would benefit from visualization
2. Add a placeholder comment: `<!-- CHART: data description for chart generation -->`
3. The platform will replace it with rendered SVG

### Best Practices for Image Placement

- Place images RIGHT AFTER the paragraph that references them
- Always wrap in `<figure>` with descriptive `<figcaption>`
- Use `max-width: 100%` to ensure responsive scaling
- Group multi-image comparisons in a flex row:
  ```html
  <div style="display:flex; gap:1em; flex-wrap:wrap; justify-content:center;">
    <figure style="flex:1; min-width:250px;"><img ...><figcaption>A</figcaption></figure>
    <figure style="flex:1; min-width:250px;"><img ...><figcaption>B</figcaption></figure>
  </div>
  ```

## Table Patterns

### Comparison Table
```html
<div class="table-wrapper">
<table>
  <thead>
    <tr><th>Dimension</th><th>Option A</th><th>Option B</th></tr>
  </thead>
  <tbody>
    <tr><td>Cost</td><td>$100/mo</td><td class="highlight">$75/mo</td></tr>
    <tr><td>Performance</td><td class="positive">High</td><td>Medium</td></tr>
  </tbody>
</table>
</div>
```

### Data Table with Trend Indicators
```html
<div class="table-wrapper">
<table>
  <thead><tr><th>Quarter</th><th>Revenue</th><th>YoY Growth</th></tr></thead>
  <tbody>
    <tr><td>Q1</td><td>$20B</td><td class="positive">+45%</td></tr>
    <tr><td>Q2</td><td>$24B</td><td class="positive">+52%</td></tr>
  </tbody>
</table>
</div>
```

## Quality Checklist

Before finalizing an HTML report:

- [ ] Is the `<style>` block included at the top?
- [ ] Is the `<article class="research-report">` wrapper present?
- [ ] Are all images in `<figure>` with `<figcaption>`?
- [ ] Are tables wrapped in `<div class="table-wrapper">` for mobile scroll?
- [ ] Are citations linked as `<sup><a href="#src-N">[N]</a></sup>`?
- [ ] Are callout boxes used for key insights (not overused)?
- [ ] Is the report responsive (no fixed widths)?
- [ ] Are colors used semantically (positive=green, negative=red, highlight=yellow)?
- [ ] If research involved visual data, are image placeholders included?
- [ ] Is the Sources section at the end with numbered references?

## Common Mistakes to Avoid

- ❌ Outputting markdown inside HTML (e.g., `## heading` instead of `<h2>heading</h2>`)
- ❌ Using inline styles excessively — prefer the CSS classes defined in the template
- ❌ Forgetting the `<style>` block at the top
- ❌ Using fixed pixel widths — use `max-width: 100%` for responsive
- ❌ Embedding huge base64 images inline — use placeholders instead
- ❌ Over-styling to the point of distraction — reports should be clean and professional
- ❌ Missing citations — HTML reports need citations just as much as markdown

## Output Notes

- The output is an HTML **fragment** — the platform wraps it in `<html><body>...</body></html>`
- The `<style>` block must be the FIRST element in the output
- All content must be inside `<article class="research-report">`
- Use semantic HTML5: `<section>`, `<figure>`, `<figcaption>`, `<table>`, `<blockquote>`
