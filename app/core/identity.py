"""
Assistant identity helper.

Builds the identity preamble for LLM system prompts based on user-configured
assistant_name and personality_notes.  When no custom values are set, falls
back to the default "personal assistant" identity.
"""


def identity_preamble(
    assistant_name: str | None = None,
    personality_notes: str | None = None,
    channel_hint: str | None = None,
) -> str:
    """
    Return a 1-2 sentence identity string for use at the start of system prompts.

    Examples:
      - No config:    "You are a personal assistant communicating via SMS."
      - Name only:    "You are Jarvis, a personal assistant communicating via SMS."
      - Name + notes: "You are Jarvis, a personal assistant communicating via SMS. Personality: witty and concise."
    """
    channel_suffix = f" communicating via {channel_hint}" if channel_hint else ""
    if assistant_name:
        base = f"You are {assistant_name}, a personal assistant{channel_suffix}."
    else:
        base = f"You are a personal assistant{channel_suffix}."

    if personality_notes:
        base += f" Personality: {personality_notes}."

    return base
