"""The thinking-backchannel processor: decides when to say "one sec".

The mirror image of :mod:`pipecat_backchannel`: that plugin fills the pauses
the *user* leaves while still deciding what to say. This one fills the
silences the *bot* leaves while working out its reply — waiting for the first
token, and waiting for tools to run. Agentic setups make those silences long
and frequent: an agent that calls three tools before answering can leave the
user listening to nothing for tens of seconds, spread over several gaps.

The gate is a frame state machine, not an audio one, and that is what makes it
cheap: no VAD, no turn model, no ONNX. The pipeline already knows when the bot
is working and says so in frames.

**Arming and disarming.** Two frames mean the bot went to work:
``LLMFullResponseStartFrame`` (a reply is being generated — the TTFT wait) and
``FunctionCallInProgressFrame`` (a tool is running). Three things mean the
silence is over: the reply's text or audio arriving (``TTSTextFrame``,
``TTSAudioRawFrame``, ``TTSStartedFrame``), the tool finishing
(``FunctionCallResultFrame``), or the reply ending (``LLMFullResponseEndFrame``).
A user barge-in (``StartInterruptionFrame``) cancels everything.

**Why downstream of the TTS service.** Every frame this gate needs is visible
there: the LLM and function-call frames flow through the TTS (queued behind any
audio still playing, so they arrive exactly when the output goes quiet), and
the TTS frames are its own output. Upstream of the TTS the audio isn't visible,
and the gate would have to guess when a fire was too late.

**Why the direction filter matters.** The output transport re-broadcasts media
frames upstream for observers — the assistant aggregator among them — so this
processor sees its *own* clips come back that way. A real reply always arrives
DOWNSTREAM, because the TTS that produced it sits upstream of the gate. State
changes therefore only listen downstream, with one exception:
``InterruptionFrame`` (``StartInterruptionFrame`` before pipecat 1.15) is a
system frame and is honored from either side.

**Taking no turn.** Like its sibling, this gate never touches the LLM context.
Clips play with ``append_to_context=False``, so the bot's conversation history
has no record that anything happened: a filler isn't a turn and must not be
remembered as one.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

try:  # renamed in pipecat 1.15; the old name was removed
    from pipecat.frames.frames import InterruptionFrame
except ImportError:  # pipecat < 1.15
    from pipecat.frames.frames import StartInterruptionFrame as InterruptionFrame

from pipecat_backchannel.frames import PlayCachedClipFrame
from pipecat_backchannel.library import ClipLibrary

from pipecat_thinking_backchannel.clips import DEFAULT_CLIP_GROUPS


@dataclass
class ThinkingParams:
    """Tuning for :class:`ThinkingBackchannelProcessor`.

    Parameters:
        llm_delay_s: How long to wait for the first token before saying
            something. Below this, silence reads as normal turn-taking; above
            it, it starts to read as a hang. Around 1.0s: TTFT short of this
            doesn't need covering.
        tool_delay_s: How long to wait for a running tool before saying
            something. Tools are known to be slow, so this can be a little
            shorter than the LLM delay.
        cooldown_s: Minimum time between two clips. This and
            ``max_per_episode`` are the two dials for how chatty the bot is
            while it works.
        fire_probability: Chance of firing at an otherwise eligible moment.
            Below 1.0 because a bot that narrates every single gap sounds
            mechanical; humans skip some.
        max_per_episode: Most clips within one episode — from the moment the
            bot starts working on a reply to the moment real speech or the end
            of the reply resets it. A ten-tool agent must not say "one sec"
            ten times.
    """

    llm_delay_s: float = 1.0
    tool_delay_s: float = 0.8
    cooldown_s: float = 4.0
    fire_probability: float = 0.9
    max_per_episode: int = 2


#: Which clip group answers which kind of waiting. Groups are named after the
#: conversational function so a custom inventory can keep the same names.
_CLIP_GROUP_FOR = {"llm": "thinking", "tool": "working"}


class ThinkingBackchannelProcessor(FrameProcessor):
    """Fires filler clips while the bot is thinking or running tools.

    Passes every frame through unchanged and never touches the LLM context —
    the bot's conversation history has no idea this happened, which is exactly
    right: a filler isn't a turn and shouldn't be remembered as one.

    :class:`~pipecat_thinking_backchannel.plugin.ThinkingBackchannel` builds
    and places one of these for you. Constructing it directly means placing it
    yourself: downstream of your TTS service, with a
    :class:`~pipecat_backchannel.player.ClipPlayer` after it sharing the same
    :class:`~pipecat_backchannel.library.ClipLibrary`.
    """

    def __init__(
        self,
        *,
        params: ThinkingParams | None = None,
        clips: ClipLibrary | None = None,
        **kwargs,
    ):
        """Initialize the processor.

        Args:
            params: Tuning. Defaults to :class:`ThinkingParams`.
            clips: The clip library, shared with the
                :class:`~pipecat_backchannel.player.ClipPlayer`. Supplies both
                the inventory to choose from and the readiness signal — the
                gate stays shut until every clip has audio.
        """
        super().__init__(**kwargs)
        self._params = params or ThinkingParams()
        self._clips = clips or ClipLibrary(groups=DEFAULT_CLIP_GROUPS)

        #: Set to ``False`` to go quiet without touching the pipeline, e.g.
        #: for stretches of a call where silence while working is better.
        self.enabled = True

        self._pending: dict[str, asyncio.Task] = {}
        self._last_fired_at = float("-inf")
        self._recent_clip: str | None = None
        self._fired_this_episode = 0

        known = set(_CLIP_GROUP_FOR.values())
        if not known & set(self._clips.groups):
            logger.warning(
                f"ThinkingBackchannel: clip groups {list(self._clips.groups)} have none of "
                f"{sorted(known)}; falling back to the first group at fire time"
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Watch the bot's progress; pass every frame through untouched."""
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # The user barged in. Whatever the bot was doing, the conversation
            # moved on: cancel the pending fire and start a fresh episode.
            await self._disarm_all()
            self._fired_this_episode = 0

        elif direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, LLMFullResponseStartFrame):
                self._arm("llm")

            elif isinstance(frame, FunctionCallInProgressFrame):
                # Re-arming on every call refreshes the deadline, which is what
                # parallel tool batches want: each call is a fresh wait.
                self._arm("tool")

            elif isinstance(frame, FunctionCallResultFrame):
                # The tool finished. If the result re-triggers the LLM, the
                # next LLMFullResponseStartFrame re-arms; if not, stay quiet.
                await self._disarm("tool")

            elif isinstance(frame, (AggregatedTextFrame, TTSAudioRawFrame, TTSStartedFrame)):
                # Real reply text or audio is flowing — this is the one signal
                # that can never be a passthrough of the user's own words:
                # transcripts arrive as TranscriptionFrame, which is not a
                # subclass of AggregatedTextFrame, and the TTS never re-emits
                # them as its own.
                await self._disarm_all()
                self._fired_this_episode = 0

            elif isinstance(frame, LLMFullResponseEndFrame):
                await self._disarm_all()
                self._fired_this_episode = 0

        await self.push_frame(frame, direction)

    async def cleanup(self):
        """Cancel any pending fire."""
        await self._disarm_all()
        await super().cleanup()

    def _arm(self, kind: str):
        """Start (or restart) the wait for ``kind``."""
        if not self.enabled or not self._clips.ready:
            return
        old = self._pending.get(kind)
        if old is not None and not old.done():
            old.cancel()
        self._pending[kind] = self.create_task(self._fire_later(kind))

    async def _disarm(self, kind: str):
        task = self._pending.pop(kind, None)
        if task is not None and not task.done():
            await self.cancel_task(task)

    async def _disarm_all(self):
        for kind in list(self._pending):
            await self._disarm(kind)

    async def _fire_later(self, kind: str):
        delay = self._params.llm_delay_s if kind == "llm" else self._params.tool_delay_s
        await asyncio.sleep(delay)

        # The sleep is cancelled on disarm, so everything past this point is a
        # race with a frame arriving at the same moment — cheap checks, not
        # absolute truth. The residual window is one clip marker in flight,
        # which the transport handles like any other audio on interruption.
        now = time.monotonic()
        if now - self._last_fired_at < self._params.cooldown_s:
            logger.debug("ThinkingBackchannel: skip, cooldown")
            return
        if self._fired_this_episode >= self._params.max_per_episode:
            logger.debug("ThinkingBackchannel: skip, episode budget spent")
            return
        if random.random() > self._params.fire_probability:
            logger.debug("ThinkingBackchannel: skip, probability gate")
            return

        clip = self._pick_clip(kind)
        self._last_fired_at = now
        self._fired_this_episode += 1
        logger.debug(f"ThinkingBackchannel: firing {clip!r} ({kind})")
        await self.push_frame(PlayCachedClipFrame(clip), FrameDirection.DOWNSTREAM)

    def _pick_clip(self, kind: str) -> str:
        group = _CLIP_GROUP_FOR[kind]
        pool = list(self._clips.groups.get(group) or [])
        if not pool:
            pool = next((list(v) for v in self._clips.groups.values() if v), [])
        fresh = [c for c in pool if c != self._recent_clip]
        clip = random.choice(fresh or pool)
        self._recent_clip = clip
        return clip
