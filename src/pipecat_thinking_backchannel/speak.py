"""The default action: speak a filler through the real TTS.

The signal processor only reports that the bot is quietly working. This module
ships the sensible default response: queue a ``TTSSpeakFrame`` on your real TTS
service, with ``append_to_context=False`` so the filler stays out of the LLM
context. Because it goes through the actual TTS pipeline, turn tracking, RTVI
events and interruptions all behave exactly as they do for any other utterance.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from pipecat.frames.frames import TTSSpeakFrame

__all__ = ["DEFAULT_PHRASES", "speak_filler"]

#: The default fillers, by signal kind.
DEFAULT_PHRASES: dict[str, tuple[str, ...]] = {
    "llm": (
        "Let me think.",
        "Hmm, give me a second.",
        "Good question.",
    ),
    "tool": (
        "Let me check on that.",
        "One moment.",
        "Looking into it.",
    ),
}


def speak_filler(
    tts,
    phrases_by_kind: Mapping[str, Sequence[str]] = DEFAULT_PHRASES,
    *,
    pick=random.choice,
):
    """Return an ``on_bot_working_idle`` handler that speaks a filler.

    Args:
        tts: The TTS service to queue the filler on. It is the bot's real
            voice, so the filler is indistinguishable from any other reply.
        phrases_by_kind: Fillers by signal kind (``"llm"``, ``"tool"``). Kinds
            without phrases stay silent.
        pick: How to choose a phrase from a group. Defaults to
            ``random.choice``; replace it to weight or rotate.

    Returns:
        An async handler suitable for ``add_event_handler``::

            idle.add_event_handler("on_bot_working_idle", speak_filler(tts))
    """

    async def on_bot_working_idle(processor, kind, function_name=None):
        phrases = phrases_by_kind.get(kind)
        if phrases:
            await tts.queue_frame(TTSSpeakFrame(pick(phrases), append_to_context=False))

    return on_bot_working_idle
