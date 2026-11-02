"""Where the thinking-backchannel processors go in a pipeline.

All of them want the same spot: directly downstream of the TTS service. The
recorder needs the TTS's raw output to fill the clip library, the gate needs
the TTS's frames to know what the bot is doing, and the player must be in the
audio path to play clips. One position serves all three, so the caller never
restates it.

Every failure raises. A backchannel that placed itself wrongly makes no sound
and reports nothing, which is indistinguishable from one that simply decided
not to fire — the single worst failure mode this library has.
"""

from __future__ import annotations

from collections.abc import Sequence

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_output import BaseOutputTransport

_MANUAL_HINT = (
    "Place them by hand instead, sharing one ClipLibrary between them:\n"
    "    from pipecat_thinking_backchannel.gate import ThinkingBackchannelProcessor\n"
    "    from pipecat_backchannel.recorder import ClipRecorder  # after your TTS\n"
    "    from pipecat_backchannel.player import ClipPlayer      # then this"
)


class ThinkingBackchannelPlacementError(RuntimeError):
    """Raised when the processors can't be placed."""


def _last_index(processors: Sequence[FrameProcessor], kind: type) -> int | None:
    found = None
    for i, p in enumerate(processors):
        if isinstance(p, kind):
            found = i
    return found


def insert_after_tts(
    processors: Sequence[FrameProcessor],
    speakers: Sequence[FrameProcessor],
) -> list[FrameProcessor]:
    """Return a new list with ``speakers`` inserted after the TTS service.

    Args:
        processors: The pipeline as written by the caller. Not modified.
        speakers: The processors to insert, in the order they must appear —
            the recorder first (it must see the TTS's raw output), then the
            gate, then the player.

    Returns:
        A new list.

    Raises:
        ThinkingBackchannelPlacementError: No TTS service and no output
            transport found, so there is nowhere to watch and nothing to play
            from.
    """
    out = list(processors)

    tts_at = _last_index(out, TTSService)
    if tts_at is not None:
        out[tts_at + 1 : tts_at + 1] = list(speakers)
        return out

    output_at = _last_index(out, BaseOutputTransport)
    if output_at is None:
        raise ThinkingBackchannelPlacementError(
            "No TTS service or output transport found in the pipeline, so there is "
            "nowhere to watch the bot work and nowhere to play clips. Anchors are only "
            "looked for at the top level — a service nested inside a ParallelPipeline "
            "is invisible here.\n" + _MANUAL_HINT
        )
    out[output_at:output_at] = list(speakers)
    return out
