# Research Brief Prompt

You will be given messages exchanged between yourself and the user.
Your job is to translate these messages into a detailed, concrete research brief that will guide the research process.

<Messages>
{messages}
</Messages>

Today's date is {date}.
{skill_context}

Return a single research brief that will guide all subsequent research.

Guidelines:
1. Maximize Specificity and Detail
   - Include ALL known user preferences and explicitly list key dimensions to consider.
   - Preserve every detail the user has provided.

2. Fill in Unstated But Necessary Dimensions as Open-Ended
   - If certain attributes are essential for a meaningful output but unspecified, state they are open-ended.

3. Avoid Unwarranted Assumptions
   - If the user has not provided a particular detail, do not invent one.
   - State the lack of specification and guide the researcher to treat it as flexible.

4. Use the First Person
   - Phrase the request from the user's perspective.

5. Sources
   - For academic/scientific queries, prefer original papers over secondary summaries.
   - For product research, prefer official sites over aggregator blogs.
   - If the query is in a specific language, prioritize sources in that language.
