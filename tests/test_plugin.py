"""The facade and the full marker-to-audio path."""

from __future__ import annotations

from pipecat.frames.frames import (
    LLMFullResponseStartFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat_backchannel.library import ClipLibrary
from pipecat_backchannel.player import ClipPlayer

from pipecat_thinking_backchannel.clips import DEFAULT_CLIP_GROUPS
from pipecat_thinking_backchannel.gate import ThinkingBackchannelProcessor, ThinkingParams
from tests.conftest import (
    FAST_PARAMS,
    SAMPLE_RATE,
    MemoryCache,
    Sink,
    _setup,
    _start,
    fired,
    send,
    settle,
)


async def _wire_chain(clips: ClipLibrary):
    """gate -> markers(sink) -> player -> out(sink), started, fast timings.

    The middle sink sits between gate and player, so it sees the markers the
    player is about to turn into audio; the last one sees only audio.
    """
    gate = ThinkingBackchannelProcessor(params=ThinkingParams(**FAST_PARAMS), clips=clips)
    markers = Sink()
    player = ClipPlayer(clips=clips, volume=1.0)
    out = Sink()
    await _setup(gate, markers, player, out)
    await _start(gate, markers, player, out)
    return gate, markers, out


def _library(groups=None) -> ClipLibrary:
    return ClipLibrary(
        groups=groups or DEFAULT_CLIP_GROUPS,
        cache=MemoryCache(prefilled=True),
    )


async def test_player_turns_markers_into_tts_output():
    """End to end: gate fires, player plays, context sees nothing speakable."""
    clips = _library()
    await clips.load(SAMPLE_RATE)
    gate, markers, out = await _wire_chain(clips)

    await send(gate, LLMFullResponseStartFrame())
    await settle()

    assert len(fired(markers)) == 1
    started = [f for f in out.frames if isinstance(f, TTSStartedFrame)]
    stopped = [f for f in out.frames if isinstance(f, TTSStoppedFrame)]
    audio = [f for f in out.frames if f.__class__.__name__ == "TTSAudioRawFrame"]
    assert len(started) == 1 and len(stopped) == 1 and len(audio) >= 1
    # A clip must never be recorded as a turn in the LLM context.
    assert started[0].append_to_context is False
    # The marker itself must never leak past the player.
    assert fired(out) == []


async def test_custom_clip_groups_reach_the_gate():
    clips = _library(groups={"thinking": ["Uh."], "working": ["Hang on."]})
    await clips.load(SAMPLE_RATE)
    gate, markers, out = await _wire_chain(clips)

    await send(gate, LLMFullResponseStartFrame())
    await settle()
    assert [c.text for c in fired(markers)] == ["Uh."]
    assert len([f for f in out.frames if isinstance(f, TTSStartedFrame)]) == 1
