# Clarify With User Prompt

These are the messages exchanged so far between you and the user:
<Messages>
{messages}
</Messages>

Today's date is {date}.

Assess whether you need to ask a clarifying question, or if the user has already provided enough information for you to start research.

IMPORTANT: If you can see in the message history that you have already asked a clarifying question, you almost always do not need to ask another one. Only ask another question if ABSOLUTELY NECESSARY.

If there are acronyms, abbreviations, or unknown terms, ask the user to clarify.

Guidelines for clarification questions:
- Be concise while gathering all necessary information
- Use bullet points or numbered lists if appropriate
- Don't ask for unnecessary information the user has already provided

Respond in valid JSON format with these exact keys:
"need_clarification": boolean,
"question": "<question to ask the user>",
"verification": "<verification that you will start research>"

If clarification is needed: need_clarification=true, question="...", verification=""
If no clarification needed: need_clarification=false, question="", verification="<acknowledgement and summary>"
