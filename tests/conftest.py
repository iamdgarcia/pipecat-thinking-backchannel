"""Shared fakes and wiring helpers for the test suite.

The controller is a frame state machine, so tests drive it with frames and a
clock: no VAD, no turn model, no audio inference anywhere. The signal lands in
a captured list via the event system, exactly as an app would wire it.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import Frame, StartFrame
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.utils.asyncio.task_manager import TaskManager

from pipecat_thinking_backchannel.controller import BotWorkingIdleProcessor

#: Fast timings so tests sleep in milliseconds, not seconds.
FAST_PARAMS = {
    "llm_delay_s": 0.1,
    "tool_delay_s": 0.1,
    "cooldown_s": 0.2,
    "fire_probability": 1.0,
    "max_per_episode": 2,
}


class Sink(FrameProcessor):
    """Collects everything that passes through, and passes it on."""

    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


async def _setup(*processors: FrameProcessor):
    task_manager = TaskManager(loop=asyncio.get_running_loop())
    setup = FrameProcessorSetup(
        clock=SystemClock(), task_manager=task_manager, pipeline_worker=None
    )
    for p in processors:
        await p.setup(setup)
    for a, b in pairwise(processors):
        a.link(b)


async def make_controller(params_extra: dict | None = None):
    """A started controller wired to a sink, with the event captured.

    Returns (controller, sink, events) where events is the list of
    ``(kind, function_name)`` tuples the handler received.
    """
    from pipecat_thinking_backchannel.controller import BotWorkingIdleParams

    params = BotWorkingIdleParams(**(FAST_PARAMS | (params_extra or {})))
    controller = BotWorkingIdleProcessor(params=params)
    sink = Sink()
    await _setup(controller, sink)

    events: list[tuple] = []

    async def on_bot_working_idle(processor, kind, function_name=None):
        events.append((kind, function_name))

    controller.add_event_handler("on_bot_working_idle", on_bot_working_idle)
    await _start(controller, sink)
    return controller, sink, events


async def _start(*processors: FrameProcessor):
    """Push a StartFrame through the chain, as a pipeline would."""
    start = StartFrame(audio_in_sample_rate=24000, audio_out_sample_rate=24000)
    await processors[0].queue_frame(start, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.05)


async def send(processor: FrameProcessor, frame: Frame, direction=FrameDirection.DOWNSTREAM):
    await processor.queue_frame(frame, direction)
    await asyncio.sleep(0.02)


async def settle(seconds: float = 0.25):
    """Wait long enough for any pending fire to land (FAST delays are 0.1s)."""
    await asyncio.sleep(seconds)
