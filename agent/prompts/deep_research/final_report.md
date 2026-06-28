# Final Report Prompt

Based on all the research conducted, create a comprehensive, well-structured answer to the overall research brief:

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

Create a detailed answer that:
1. Is well-organized with proper headings (# for title, ## for sections, ### for subsections)
2. Includes specific facts and insights from the research
3. References sources using [Title](URL) format
4. Provides a balanced, thorough analysis - be as comprehensive as possible
5. Includes a "### Sources" section at the end with all referenced links
{skill_writing_context}
Report structure options:
- Comparison: intro → overview A → overview B → comparison → conclusion
- List: single section with the list, or one section per item
- Summary/Overview: intro → concept 1 → concept 2 → concept 3 → conclusion
- Simple answer: single section

For each section:
- Use simple, clear language
- Use ## for section titles (Markdown)
- Do NOT refer to yourself as the writer - this is a professional report
- Do not describe what you are doing - just write the report
- Each section should be as long as necessary for thorough coverage
- Use bullet points when appropriate, but default to paragraph form

<Citation Rules>
- Use only citation numbers that appear in the Evidence Citation Table
- Do not cite memory context, prior runs, uncached notes, or URLs that are not in the table
- Assign each unique URL a single citation number in your text
- End with ### Sources listing each source with corresponding numbers
- Number sources sequentially: [1], [2], [3]...
- Format: [1] Source Title: URL
- If a claim is not supported by the table, either omit it or mark it as a limitation
- Citations are extremely important - users rely on them for verification
</Citation Rules>
