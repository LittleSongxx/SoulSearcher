---
name: science-communication
description: Translates academic research into audience-tailored formats including lay summaries, policy briefs, press releases, social media threads, and visual abstracts.
version: "1.0.0"
tags: [academic, communication, writing]
---

# Science Communication Skill

## Overview

This skill transforms academic research into audience-appropriate communication formats. It maintains scientific accuracy while adapting language, structure, depth, and emphasis for different readerships. The skill supports five output formats, each with its own tone, structure, and style guidelines.

## Supported Output Formats

| Format | Audience | Length | Tone | Key Elements |
|---|---|---|---|---|
| **Lay Summary** | General public | 300-500 words | Accessible, engaging | Why it matters, what was found, in plain language |
| **Policy Brief** | Decision-makers | 1-2 pages | Authoritative, actionable | Problem, evidence, options, recommendation |
| **Press Release** | Journalists/media | 400-600 words | Newsworthy, quotable | Hook, key finding, quote, context |
| **Twitter/X Thread** | Academic Twitter | 5-8 tweets | Conversational, punchy | One key insight per tweet, visual, call to action |
| **Visual Abstract** | All audiences | 1 image + caption | Visual-first | Core concept, key result, take-home message |

## When to Use This Skill

**Use when:**

- User provides a paper and says "summarize for the public", "explain this simply", "make this accessible"
- User says "write a press release", "prepare a media statement", "draft a news article"
- User wants to share research on social media: "create a Twitter thread", "write a LinkedIn post"
- User says "policy brief", "briefing note", "executive summary for policymakers"
- User wants a visual abstract or graphical summary
- The `academic-paper-review` skill has been run and user wants to disseminate findings
- User is preparing a talk or presentation for a non-specialist audience

## Workflow

### Phase 0: Input

This skill works best when you already have a paper review or decomposition available. If the user hasn't run `academic-paper-review` or `paper-decomposition`, do a rapid read of the paper (abstract + introduction + results + conclusion) to extract:

- Core research question
- Key finding / main result
- Significance / why it matters
- One surprising or counterintuitive aspect
- Limitations (mentioned honestly)

### Phase 1: Audience Adaptation

#### For Lay Audience (Lay Summary)

Principles:
- **Start with WHY**: Lead with the problem or question people care about, not the method
- **No jargon**: Replace technical terms with everyday analogies. If you must use a technical term, define it in one sentence
- **Concrete over abstract**: Use specific examples, not general principles
- **Human interest**: Connect findings to people's lives

Structure:
```
[Hook: relatable problem or surprising fact]
[Context: what we already knew, in simple terms]
[The New Finding: what the researchers discovered]
[Why It Matters: implications for everyday life]
[Caveat: one honest limitation, stated plainly]
```

#### For Policy Brief

Principles:
- **Problem-first**: Policy makers need to know what problem this addresses
- **Evidence-based options**: Present 2-3 policy options grounded in the research
- **Actionable recommendations**: Be specific about what should be done
- **Consider feasibility**: Acknowledge implementation challenges

Structure:
```
EXECUTIVE SUMMARY (3 bullet points)

1. PROBLEM STATEMENT
   - What is the issue? Who is affected? What is the scale?

2. RESEARCH FINDINGS
   - What does the evidence show? How strong is the evidence?

3. POLICY OPTIONS
   - Option A: [description, pros, cons, feasibility]
   - Option B: [description, pros, cons, feasibility]

4. RECOMMENDATION
   - Preferred option with justification

5. LIMITATIONS AND UNCERTAINTIES
   - What we don't know, caveats, need for further research
```

#### For Press Release

Principles:
- **Newsworthiness**: Why should anyone care? What's new?
- **Inverted pyramid**: Most important info first, details later
- **Quote-worthy**: Include at least one quotable statement from "the researchers"
- **No hype**: Avoid overclaiming. Use measured language

Structure:
```
HEADLINE: [Active voice, key finding, < 15 words]

DATELINE — [Lead paragraph: who, what, when, where, why in 1-2 sentences]

[Quote from lead researcher — authentic, not marketing-speak]

[2-3 paragraphs of supporting detail — methodology, context, significance]

[Boilerplate: about the institution / funding source]

Media Contact: [name, email]
Paper Reference: [citation or DOI]
```

#### For Twitter/X Thread

Principles:
- **One idea per tweet**: Each tweet is self-contained and valuable
- **First tweet is the hook**: Make people want to click "Show more"
- **Use numbers and specifics**: "94.2% accuracy" not "highly accurate"
- **Include visuals**: Charts, diagrams, or paper figures (with attribution)
- **End with CTA**: Link to paper, invite discussion

Structure:
```
Tweet 1 (Hook): Surprising finding or counterintuitive result
Tweet 2: What was previously known / the problem
Tweet 3: What the researchers did (1 sentence)
Tweet 4: Key result with number
Tweet 5: Why this matters / what changes
Tweet 6: One limitation or open question
Tweet 7 (optional): Link to paper + "What do you think?"
```

#### For Visual Abstract

Principles:
- **One core concept**: Don't try to show everything
- **Left-to-right or top-to-bottom flow**: Guide the eye naturally
- **Minimal text**: Labels only, no paragraphs
- **Color coding**: Consistent colors for consistent concepts

Generate a text description that the user can give to a designer or use with `image-generation`:

```
Visual Abstract Description:
- Layout: Horizontal, 3 panels left to right
- Panel 1 (Problem): [description of visual element]
- Panel 2 (Method): [description of visual element]
- Panel 3 (Result): [description of visual element]
- Color scheme: [suggested colors and their meanings]
- Take-home message: [1 sentence to place at bottom]
```

### Phase 2: Generate Chosen Format(s)

Ask the user which format(s) they want, or infer from context. Generate one or more formats.

### Phase 3: Quality Review

Before finalizing, check:

#### Lay Summary Checklist
- [ ] Can a 15-year-old understand this?
- [ ] Are all acronyms spelled out or avoided?
- [ ] Does it start with WHY, not WHAT?
- [ ] Is there a human connection?

#### Policy Brief Checklist
- [ ] Is the problem clearly stated with scale and affected populations?
- [ ] Are policy options evidence-based (not just opinion)?
- [ ] Is there a clear, actionable recommendation?
- [ ] Are limitations honestly disclosed?

#### Press Release Checklist
- [ ] Is the headline under 15 words?
- [ ] Is the lead paragraph complete (who, what, when, where, why)?
- [ ] Is the quote authentic-sounding?
- [ ] Is there no hype or overclaiming?

#### Twitter Thread Checklist
- [ ] First tweet is attention-grabbing?
- [ ] Each tweet is self-contained?
- [ ] Numbers are specific (not vague)?
- [ ] Visual element mentioned or prepared?
- [ ] Call to action at end?

## Integration with Other Skills

- **After `academic-paper-review`**: Transform the detailed review into accessible formats
- **After `paper-decomposition`**: Use the structured extraction to identify the most communicable insights
- **Before `podcast-generation`**: Use the lay summary as script foundation
- **Before `image-generation`**: Use the visual abstract description as an image prompt

## Quality Checklist

- [ ] Paper content accurately represented (no distortion)
- [ ] Target audience clearly identified
- [ ] Format-appropriate structure followed
- [ ] Technical terms explained or avoided appropriately for audience
- [ ] Limitations disclosed honestly
- [ ] No hype, no overclaiming, no sensationalism
- [ ] Call to action or take-home message present
- [ ] Output saved (ensure `mkdir -p /mnt/user-data/outputs`) to `/mnt/user-data/outputs/scicomm-{format}-{paper-slug}.md`
