"""The one thing you configure, and the one thing you call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pipecat.processors.frame_processor import FrameProcessor
from pipecat_backchannel.library import ClipLibrary
from pipecat_backchannel.player import ClipPlayer
from pipecat_backchannel.prewarm import prewarm_library
from pipecat_backchannel.recorder import ClipRecorder

from pipecat_thinking_backchannel.gate import ThinkingBackchannelProcessor, ThinkingParams
from pipecat_thinking_backchannel.placement import insert_after_tts


class ThinkingBackchannel:
    """Adds thinking-backchannels to a pipeline.

    The companion to :class:`pipecat_backchannel.Backchannel`: that one makes
    the bot sound like it's listening while the user talks; this one makes it
    sound like it's working while it thinks. It plays short fillers ("hmm",
    "one sec", "let me check") into the silences an agentic pipeline leaves —
    the wait for the first token, and the wait for each tool — so a multi-tool
    agent stops sounding like it hung.

    Build one, call it with your processor list, and use what comes back::

        thinking = ThinkingBackchannel()

        pipeline = Pipeline(thinking([
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,          # with function-calling tools
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]))

    With no arguments the clips are recorded from your own TTS service, once,
    and cached — so the voice matches, and there is nothing to configure. The
    gate itself is a frame state machine: no VAD, no turn model, nothing to
    run while the user speaks.

    **Build one per process, not per session.** It holds configuration and the
    clip library, both of which are worth sharing; each call builds fresh
    processors for that pipeline, which is where the per-conversation state
    lives.
    """

    def __init__(
        self,
        *,
        params: ThinkingParams | None = None,
        clip_groups: Mapping[str, Sequence[str]] | None = None,
        cache=None,
        volume: float = 1.0,
    ):
        """Initialize the thinking backchannel.

        Args:
            params: Timing and frequency of the gate. Defaults to
                :class:`~pipecat_thinking_backchannel.gate.ThinkingParams`.
            clip_groups: The clip inventory, grouped by conversational function
                (``thinking`` for the first-token wait, ``working`` for tools).
            cache: Where clips are kept between runs. Defaults to
                :class:`~pipecat_backchannel.cache.FileClipCache`.
            volume: How loud the clips play, where ``1.0`` is exactly as
                recorded. Unlike the user-side backchannel — which sits under
                someone else's speech and defaults to 0.6 — here the bot has
                the floor and the silence, so full volume is right.
        """
        self._library = ClipLibrary(groups=clip_groups, cache=cache)
        self._volume = volume
        self._params = params
        # Clips can be pre-recorded or synthesized; without either they have to
        # be recorded from a TTS service, which needs a processor in the
        # pipeline.
        self._needs_recorder = self._library.synthesizer is None

    @property
    def clips(self) -> ClipLibrary:
        """The clip library shared by every pipeline this is added to."""
        return self._library

    async def prewarm(
        self, *, tts, sample_rate: int | None = None, budget_s: float = 120.0
    ) -> bool:
        """Record the clips now, so no session has to.

        Entirely optional. Without it the first session records them itself,
        held in startup while it does — correct, but a wait for whoever connects
        first. Call this at app startup to move that cost out of the request
        path. Returns straight away once the clips are cached, so it is cheap to
        call unconditionally.

        Args:
            tts: A pipecat ``TTSService``, used and then shut down. Build a
                throwaway instance for this — do not pass one a session will use.
            sample_rate: Rate to record at. Taken from ``tts`` when omitted, so
                the clips match what that service produces. Only pass it when
                the service negotiates its rate from the pipeline instead of
                being configured with one.
            budget_s: Hard ceiling, so a misbehaving service can't hang startup.

        Returns:
            Whether every clip ended up with audio.
        """
        return await prewarm_library(
            self._library, tts=tts, sample_rate=sample_rate, budget_s=budget_s
        )

    def __call__(self, processors: Sequence[FrameProcessor]) -> list[FrameProcessor]:
        """Return a new processor list with a thinking backchannel inserted.

        Call this once per pipeline. Each call builds its own processors — they
        hold per-conversation state — over the one shared clip library, so the
        clips are loaded once per process however many sessions you run.

        Args:
            processors: Your pipeline, as you'd otherwise pass it to
                ``Pipeline``. Not modified.

        Returns:
            A new list, ready for ``Pipeline(...)``.

        Raises:
            ~pipecat_thinking_backchannel.placement.ThinkingBackchannelPlacementError:
                The position couldn't be worked out from the list.
        """
        gate = ThinkingBackchannelProcessor(params=self._params, clips=self._library)
        speakers: list[FrameProcessor] = []
        if self._needs_recorder:
            speakers.append(ClipRecorder(clips=self._library))
        speakers.append(gate)
        speakers.append(ClipPlayer(clips=self._library, volume=self._volume))

        return insert_after_tts(processors, speakers)
