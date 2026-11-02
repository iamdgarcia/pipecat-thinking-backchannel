"""The default clip inventory.

Two groups, named for the two kinds of waiting the gate knows about:

- ``thinking`` — the wait for the LLM's first token. Short hesitations, the
  sound of someone composing a thought.
- ``working`` — the wait for a running tool. The sound of someone doing
  something and coming right back.

Give each group at least two clips, or it repeats itself. Clips are recorded
once through the pipeline's own TTS and cached, so they come out in the bot's
voice; change the voice and delete the cache directory.
"""

DEFAULT_CLIP_GROUPS = {
    "thinking": ["Hmm.", "Let me think.", "Let me see.", "Right..."],
    "working": ["One sec.", "Let me check.", "Just a moment.", "One moment."],
}
