"""The default action: speak_filler queues a real TTSSpeakFrame."""

from __future__ import annotations

from pipecat.frames.frames import TTSSpeakFrame

from pipecat_thinking_backchannel.speak import DEFAULT_PHRASES, speak_filler


class FakeTTS:
    """Records queue_frame calls instead of speaking."""

    def __init__(self):
        self.queued: list = []

    async def queue_frame(self, frame):
        self.queued.append(frame)


async def test_speak_filler_queues_a_real_tts_frame():
    tts = FakeTTS()
    handler = speak_filler(tts)
    await handler(None, "tool", "get_weather")
    assert len(tts.queued) == 1
    frame = tts.queued[0]
    assert isinstance(frame, TTSSpeakFrame)
    assert frame.text in DEFAULT_PHRASES["tool"]
    # The whole point of append_to_context=False: the filler never reaches
    # the LLM context.
    assert frame.append_to_context is False


async def test_speak_filler_varies_by_kind():
    tts = FakeTTS()
    handler = speak_filler(tts)
    await handler(None, "llm", None)
    assert tts.queued[0].text in DEFAULT_PHRASES["llm"]


async def test_speak_filler_silent_for_unknown_kind():
    tts = FakeTTS()
    handler = speak_filler(tts, {"tool": ("One moment.",)})
    await handler(None, "llm", None)
    assert tts.queued == []


async def test_speak_filler_uses_custom_phrases_and_pick():
    tts = FakeTTS()
    handler = speak_filler(tts, {"tool": ("A", "B")}, pick=lambda seq: seq[-1])
    await handler(None, "tool", None)
    assert tts.queued[0].text == "B"
