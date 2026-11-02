"""The gate: arming, disarming, and the dials in between."""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat_backchannel.library import ClipLibrary

from pipecat_thinking_backchannel.clips import DEFAULT_CLIP_GROUPS
from tests.conftest import (
    PCM,
    SAMPLE_RATE,
    MemoryCache,
    fired,
    make_gate,
    send,
    settle,
)

_TOOL = FunctionCallInProgressFrame(
    function_name="get_weather", tool_call_id="t1", arguments={}
)


async def test_fires_thinking_clip_after_llm_start():
    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await settle()
    clips = fired(sink)
    assert len(clips) == 1
    assert clips[0].text in DEFAULT_CLIP_GROUPS["thinking"]


async def test_fires_working_clip_on_tool_start():
    gate, sink = await make_gate()
    await send(gate, _TOOL)
    await settle()
    clips = fired(sink)
    assert len(clips) == 1
    assert clips[0].text in DEFAULT_CLIP_GROUPS["working"]


async def test_first_token_text_disarms():
    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await send(gate, TTSTextFrame(text="Hi!", text_type="sentence"))
    await settle()
    assert fired(sink) == []


async def test_tts_audio_disarms():
    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await send(gate, TTSAudioRawFrame(audio=PCM, sample_rate=SAMPLE_RATE, num_channels=1))
    await settle()
    assert fired(sink) == []


async def test_tts_started_disarms():
    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await send(gate, TTSStartedFrame())
    await settle()
    assert fired(sink) == []


async def test_tool_result_cancels_pending_fire():
    gate, sink = await make_gate()
    await send(gate, _TOOL)
    await send(gate, FunctionCallResultFrame(function_name="get_weather", tool_call_id="t1", arguments={}, result="sunny"))
    await settle()
    assert fired(sink) == []


async def test_end_of_response_cancels_pending_fire():
    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await send(gate, LLMFullResponseEndFrame())
    await settle()
    assert fired(sink) == []


async def test_interrupt_cancels_pending_fire():
    from pipecat.frames.frames import InterruptionFrame

    gate, sink = await make_gate()
    await send(gate, _TOOL)
    await send(gate, InterruptionFrame())
    await settle()
    assert fired(sink) == []


async def test_rearm_refreshes_deadline():
    """A second InProgress must push the deadline out, not stack a second fire."""
    gate, sink = await make_gate(params_extra={"tool_delay_s": 0.3})
    await send(gate, _TOOL)
    await asyncio.sleep(0.06)
    await send(gate, _TOOL)  # re-arms: deadline moves to ~0.4 from the start
    # Unrefreshed, the first timer would fire at ~0.32; the re-arm cancelled it.
    await settle(0.4)
    assert len(fired(sink)) == 1


async def test_cooldown_blocks_second_clip():
    gate, sink = await make_gate(params_extra={"cooldown_s": 0.5})
    await send(gate, LLMFullResponseStartFrame())  # armed ≈0.02, fires ≈0.13
    await settle(0.2)
    assert len(fired(sink)) == 1
    await send(gate, LLMFullResponseStartFrame())  # re-armed ≈0.24, would fire ≈0.34
    await settle(0.2)  # t≈0.44, still inside cooldown (ends ≈0.63)
    assert len(fired(sink)) == 1
    await settle(0.3)  # cooldown over
    await send(gate, LLMFullResponseStartFrame())  # re-armed, fires
    await settle(0.2)
    assert len(fired(sink)) == 2


async def test_max_per_episode_caps_a_run_of_iterations():
    gate, sink = await make_gate(params_extra={"max_per_episode": 1})
    await send(gate, _TOOL)
    await settle()
    assert len(fired(sink)) == 1
    # Same episode: another tool starts, but the budget is spent.
    await settle(0.25)  # let the cooldown lapse
    await send(gate, _TOOL)
    await settle()
    assert len(fired(sink)) == 1
    # Real speech resets the episode.
    await send(gate, TTSAudioRawFrame(audio=PCM, sample_rate=SAMPLE_RATE, num_channels=1))
    await settle(0.25)  # let the cooldown lapse
    await send(gate, _TOOL)
    await settle()
    assert len(fired(sink)) == 2


async def test_disabled_gate_never_fires():
    gate, sink = await make_gate()
    gate.enabled = False
    await send(gate, _TOOL)
    await settle()
    assert fired(sink) == []


async def test_zero_probability_never_fires():
    gate, sink = await make_gate(params_extra={"fire_probability": 0.0})
    await send(gate, _TOOL)
    await settle()
    assert fired(sink) == []


async def test_gate_stays_shut_until_clips_ready():
    library = ClipLibrary(groups=DEFAULT_CLIP_GROUPS, cache=MemoryCache(prefilled=False))
    gate, sink = await make_gate(clips=library)
    await send(gate, _TOOL)
    await settle()
    assert fired(sink) == []


async def test_upstream_tts_frames_do_not_disarm():
    """The transport echoes our own clips back upstream; those must be ignored."""
    from pipecat.processors.frame_processor import FrameDirection

    gate, sink = await make_gate()
    await send(gate, _TOOL)
    await send(
        gate,
        TTSAudioRawFrame(audio=PCM, sample_rate=SAMPLE_RATE, num_channels=1),
        FrameDirection.UPSTREAM,
    )
    await settle()
    assert len(fired(sink)) == 1


async def test_aggregated_text_without_tts_text_disarms():
    """skip_tts replies surface as AggregatedTextFrame, not TTSTextFrame."""
    from pipecat.frames.frames import AggregatedTextFrame

    gate, sink = await make_gate()
    await send(gate, LLMFullResponseStartFrame())
    await send(gate, AggregatedTextFrame(text="internal", text_type="sentence"))
    await settle()
    assert fired(sink) == []
