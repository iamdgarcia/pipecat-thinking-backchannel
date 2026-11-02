"""Shared fakes and wiring helpers for the test suite.

The gate is a frame state machine, so tests drive it with frames and a clock:
no VAD, no turn model, no audio inference anywhere. The clip library comes
from a prefilled in-memory cache so the readiness gate is open without a TTS.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat_backchannel.frames import PlayCachedClipFrame
from pipecat_backchannel.library import ClipLibrary

from pipecat_thinking_backchannel.clips import DEFAULT_CLIP_GROUPS
from pipecat_thinking_backchannel.gate import (
    ThinkingBackchannelProcessor,
    ThinkingParams,
)

SAMPLE_RATE = 24000
PCM = b"\x01\x02" * 10

#: Fast timings so tests sleep in milliseconds, not seconds.
FAST_PARAMS = {
    "llm_delay_s": 0.1,
    "tool_delay_s": 0.1,
    "cooldown_s": 0.2,
    "fire_probability": 1.0,
    "max_per_episode": 2,
}


class MemoryCache:
    """In-memory ClipCache. ``prefilled`` makes every lookup a hit."""

    def __init__(self, prefilled: bool = False):
        self.prefilled = prefilled
        self.stored: dict[tuple[str, int], bytes] = {}

    def get(self, text: str, sample_rate: int) -> bytes | None:
        if (text, sample_rate) in self.stored:
            return self.stored[(text, sample_rate)]
        return PCM if self.prefilled else None

    def put(self, text: str, sample_rate: int, pcm: bytes) -> None:
        self.stored[(text, sample_rate)] = pcm


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


async def _start(*processors: FrameProcessor):
    """Push a StartFrame through the chain, as a pipeline would."""
    start = StartFrame(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE)
    await processors[0].queue_frame(start, FrameDirection.DOWNSTREAM)
    await asyncio.sleep(0.05)


async def _ready_library(**kwargs) -> ClipLibrary:
    kwargs.setdefault("groups", DEFAULT_CLIP_GROUPS)
    library = ClipLibrary(cache=MemoryCache(prefilled=True), **kwargs)
    await library.load(SAMPLE_RATE)
    return library


async def make_gate(*, params_extra: dict | None = None, clips: ClipLibrary | None = None):
    """A started gate with a sink attached. Returns (gate, sink)."""
    params = ThinkingParams(**(FAST_PARAMS | (params_extra or {})))
    gate = ThinkingBackchannelProcessor(params=params, clips=clips or await _ready_library())
    sink = Sink()
    await _setup(gate, sink)
    await _start(gate, sink)
    return gate, sink


async def send(processor: FrameProcessor, frame: Frame, direction=FrameDirection.DOWNSTREAM):
    await processor.queue_frame(frame, direction)
    await asyncio.sleep(0.02)


async def settle(seconds: float = 0.3):
    """Wait long enough for any pending fire to land (FAST delays are 0.1s)."""
    await asyncio.sleep(seconds)


def fired(sink: Sink) -> list[PlayCachedClipFrame]:
    return [f for f in sink.frames if isinstance(f, PlayCachedClipFrame)]


def tts_output(sink: Sink) -> list[Frame]:
    return [f for f in sink.frames if isinstance(f, (TTSStartedFrame, TTSAudioRawFrame, TTSStoppedFrame))]
