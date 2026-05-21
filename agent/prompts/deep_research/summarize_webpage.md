# Webpage Summarization Prompt

You are tasked with summarizing the raw content of a webpage from a web search.
Create a summary that preserves the most important information.
This summary will be used by a downstream research agent.

<webpage_content>
{webpage_content}
</webpage_content>

Guidelines:
1. Identify and preserve the main topic or purpose.
2. Retain key facts, statistics, and data points.
3. Keep important quotes from credible sources.
4. Maintain chronological order for time-sensitive content.
5. Preserve lists or step-by-step instructions.
6. Include relevant dates, names, and locations.

By content type:
- News articles: who, what, when, where, why, how
- Scientific content: methodology, results, conclusions
- Opinion pieces: main arguments and supporting points
- Product pages: key features, specifications

Your summary should be about 25-30% of the original length.

Today's date is {date}.
