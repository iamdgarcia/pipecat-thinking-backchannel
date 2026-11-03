"""The signal: one processor that knows when the bot has gone quiet at work.

Place it between the LLM and the TTS. It watches the reply and function-call
frames going downstream, and the bot's actual mouth (`BotStarted/Stopped
SpeakingFrame`, re-broadcast upstream by the output transport) to know when
the bot is really silent. When a wait runs long — the first token, or a tool
— it fires an event. What happens next is the app's business:

    idle = BotWorkingIdleProcessor()

    @idle.event_handler("on_bot_working_idle")
    async def on_bot_working_idle(processor, kind, function_name):
        await tts.queue_frame(TTSSpeakFrame("Let me check on that.",
                                            append_to_context=False))

The signal/action split mirrors `UserIdleController` on the user side: this
processor only decides *that* the bot is quietly working; the handler decides
*what*, if anything, to say about it.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EagerTranscriptionFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

__all__ = ["BotWorkingIdleParams", "BotWorkingIdleProcessor"]

#: The bot went quiet waiting for its own first token.
KIND_LLM = "llm"
#: The bot went quiet while a tool was running.
KIND_TOOL = "tool"


@dataclass
class BotWorkingIdleParams:
    """Knobs for the idle signal.

    Args:
        llm_delay_s: How long the bot must be quiet waiting for its first
            token before the event fires.
        tool_delay_s: How long the bot must be quiet waiting on a tool before
            the event fires. A second parallel tool re-arms the timer instead
            of stacking a second one.
        cooldown_s: Minimum time between two events. This and
            ``max_per_episode`` are the two dials for how chatty the signal is.
        fire_probability: Chance of firing at an otherwise eligible moment.
            Below 1.0, or it sounds mechanical.
        max_per_episode: Most events within one episode, from the first arm
            until the reply ends or an interruption lands.
    """

    llm_delay_s: float = 1.0
    tool_delay_s: float = 0.8
    cooldown_s: float = 4.0
    fire_probability: float = 0.9
    max_per_episode: int = 2


class BotWorkingIdleProcessor(FrameProcessor):
    """Fires ``on_bot_working_idle(kind, function_name)`` when the bot works quietly.

    Downstream, the processor sees the reply's frames: the response start arms
    the first-token wait, ``FunctionCallInProgressFrame`` arms a tool wait (one
    timer per batch, refreshed per call), and real text, call results or the
    response end disarm everything. Upstream, it watches the transport's
    ``BotStarted/StoppedSpeakingFrame``: while the bot is actually speaking,
    nothing fires, and a wait that expires mid-speech fires once the bot goes
    quiet — the filler belongs to the silence, not to the speech.

    Event handlers available:

    - ``on_bot_working_idle(kind, function_name)``: emitted when a working
      silence has lasted long enough. ``kind`` is ``"llm"`` for the first-token
      wait or ``"tool"`` for a tool wait; ``function_name`` is set for tools.
    """

    def __init__(self, *, params: BotWorkingIdleParams | None = None):
        super().__init__()
        self._params = params or BotWorkingIdleParams()
        self._register_event_handler("on_bot_working_idle")

        self._timer: asyncio.Task | None = None
        self._kind: str | None = None
        self._function_name: str | None = None
        self._deferred: bool = False
        self._bot_speaking: bool = False
        self._last_fired_at: float | None = None
        self._fired_this_episode: int = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.UPSTREAM:
            # From the transport: the ground truth of the bot's mouth.
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                if self._deferred:
                    # The wait expired mid-speech; the filler belongs to the
                    # silence that just opened.
                    await self._fire()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._fired_this_episode = 0
            await self._arm(KIND_LLM, None, self._params.llm_delay_s)
        elif isinstance(frame, FunctionCallInProgressFrame):
            await self._arm(KIND_TOOL, frame.function_name, self._params.tool_delay_s)
        elif isinstance(frame, FunctionCallResultFrame):
            await self._disarm()
        elif isinstance(frame, (AggregatedTextFrame, TextFrame)) and not isinstance(
            frame, (TranscriptionFrame, InterimTranscriptionFrame, EagerTranscriptionFrame)
        ):
            # Real text is flowing toward the TTS; no filler belongs here.
            await self._disarm()
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._disarm()
            self._fired_this_episode = 0
        elif isinstance(frame, InterruptionFrame):
            await self._disarm()
            self._fired_this_episode = 0
            self._bot_speaking = False

        await self.push_frame(frame, direction)

    async def cleanup(self):
        """Cancel the timer at session end."""
        await self._cancel_timer()
        await super().cleanup()

    async def _arm(self, kind: str, function_name: str | None, delay: float):
        """Arm (or refresh) the single wait timer. Batches re-arm, not stack."""
        self._kind = kind
        self._function_name = function_name
        await self._cancel_timer()
        self._timer = self.create_task(self._timer_expired(delay))

    async def _timer_expired(self, delay: float):
        await asyncio.sleep(delay)
        self._timer = None
        if self._bot_speaking:
            # The bot is still talking (e.g. an intro line over a tool run);
            # hold the fire for the silence that will follow.
            self._deferred = True
            return
        await self._fire()

    async def _fire(self):
        self._deferred = False
        if self._kind is None:
            return
        now = time.monotonic()
        if self._last_fired_at is not None and now - self._last_fired_at < self._params.cooldown_s:
            return
        if self._fired_this_episode >= self._params.max_per_episode:
            return
        if random.random() > self._params.fire_probability:
            return
        self._last_fired_at = now
        self._fired_this_episode += 1
        await self._call_event_handler("on_bot_working_idle", self._kind, self._function_name)

    async def _disarm(self):
        """The silence is over (or was never silence): drop the wait."""
        await self._cancel_timer()
        self._deferred = False
        self._kind = None
        self._function_name = None

    async def _cancel_timer(self):
        if self._timer:
            await self.cancel_task(self._timer)
            self._timer = None
