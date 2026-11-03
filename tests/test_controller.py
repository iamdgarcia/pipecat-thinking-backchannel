"""The signal: arming, disarming, deferral, and the dials in between."""

from __future__ import annotations

from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from pipecat_thinking_backchannel.controller import BotWorkingIdleProcessor
from tests.conftest import Sink, _setup, make_controller, send, settle

_TOOL = FunctionCallInProgressFrame(
    function_name="get_weather", tool_call_id="t1", arguments={}
)


async def test_fires_llm_idle_when_no_token_arrives():
    controller, _, events = await make_controller()
    await send(controller, LLMFullResponseStartFrame())
    await settle()
    assert events == [("llm", None)]


async def test_text_disarms_the_llm_wait():
    controller, _, events = await make_controller()
    await send(controller, LLMFullResponseStartFrame())
    await send(controller, AggregatedTextFrame(text="Hello!", text_type="sentence"))
    await settle()
    assert events == []


async def test_transcription_frames_do_not_disarm():
    """Final transcriptions pass through on their way to the context."""
    controller, _, events = await make_controller()
    await send(controller, LLMFullResponseStartFrame())
    await send(controller, TranscriptionFrame(text="hi", user_id="", timestamp=""))
    await settle()
    assert events == [("llm", None)]


async def test_fires_tool_idle_with_function_name():
    controller, _, events = await make_controller()
    await send(controller, _TOOL)
    await settle()
    assert events == [("tool", "get_weather")]


async def test_parallel_tool_calls_rearm_not_stack():
    controller, _, events = await make_controller()
    await send(controller, _TOOL)
    await send(controller, FunctionCallInProgressFrame(
        function_name="get_time", tool_call_id="t2", arguments={}))
    await settle(0.3)
    assert events == [("tool", "get_time")]


async def test_function_call_result_disarms():
    controller, _, events = await make_controller()
    await send(controller, _TOOL)
    await send(controller, FunctionCallResultFrame(
        function_name="get_weather", tool_call_id="t1", arguments={}, result={}))
    await settle()
    assert events == []


async def test_fire_deferred_while_bot_is_speaking_then_fires_on_stop():
    controller, _, events = await make_controller()
    await send(controller, BotStartedSpeakingFrame(), direction=FrameDirection.UPSTREAM)
    await send(controller, _TOOL)
    await settle(0.15)  # timer expired while speaking -> deferred
    assert events == []
    await send(controller, BotStoppedSpeakingFrame(), direction=FrameDirection.UPSTREAM)
    await settle()
    assert events == [("tool", "get_weather")]


async def test_no_fire_while_bot_speaking_stays_suppressed():
    controller, _, events = await make_controller()
    await send(controller, BotStartedSpeakingFrame(), direction=FrameDirection.UPSTREAM)
    await send(controller, _TOOL)
    await settle(0.15)
    await send(controller, AggregatedTextFrame(text="Here you go.", text_type="sentence"))
    await send(controller, BotStoppedSpeakingFrame(), direction=FrameDirection.UPSTREAM)
    await settle()
    assert events == []


async def test_cooldown_suppresses_back_to_back_fires():
    controller, _, events = await make_controller(
        {"cooldown_s": 10.0})
    await send(controller, LLMFullResponseStartFrame())
    await settle()
    await send(controller, LLMFullResponseEndFrame())
    await send(controller, LLMFullResponseStartFrame())
    await settle()
    assert len(events) == 1


async def test_max_per_episode_caps_the_episode():
    controller, _, events = await make_controller({"max_per_episode": 1})
    await send(controller, LLMFullResponseStartFrame())
    await send(controller, _TOOL)  # same episode, second arm
    await settle(0.35)
    assert len(events) == 1


async def test_new_episode_resets_the_cap():
    controller, _, events = await make_controller({"max_per_episode": 1})
    await send(controller, LLMFullResponseStartFrame())
    await settle()
    await send(controller, LLMFullResponseEndFrame())
    await send(controller, LLMFullResponseStartFrame())
    await settle()
    assert len(events) == 2


async def test_interruption_disarms_and_resets():
    controller, _, events = await make_controller()
    await send(controller, LLMFullResponseStartFrame())
    await send(controller, InterruptionFrame())
    await settle()
    assert events == []


async def test_end_frame_disarms():
    controller, _, events = await make_controller()
    await send(controller, _TOOL)
    await send(controller, LLMFullResponseEndFrame())
    await settle()
    assert events == []


async def test_frames_pass_through_downstream_and_upstream():
    """Downstream flows on, upstream flows toward the LLM (its own sink)."""
    controller = BotWorkingIdleProcessor()
    up_sink, down_sink = Sink(), Sink()
    await _setup(up_sink, controller, down_sink)
    up_sink.link(controller)      # controller's upstream neighbor
    controller.link(down_sink)    # controller's downstream neighbor
    # Start the chain: a StartFrame wakes every internal processing task.
    from pipecat.frames.frames import StartFrame
    await send(up_sink, StartFrame(audio_in_sample_rate=24000, audio_out_sample_rate=24000))
    up_sink.frames.clear()

    await send(controller, _TOOL)
    await send(controller, BotStoppedSpeakingFrame(), direction=FrameDirection.UPSTREAM)

    assert any(isinstance(f, FunctionCallInProgressFrame) for f in down_sink.frames)
    assert any(isinstance(f, BotStoppedSpeakingFrame) for f in up_sink.frames)
