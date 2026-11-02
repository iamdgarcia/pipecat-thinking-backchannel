"""Placement: everything lands downstream of the TTS service, in order."""

from __future__ import annotations

import pytest
from pipecat.services.llm_service import LLMService
from pipecat.services.stt_service import STTService
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from pipecat_thinking_backchannel.placement import (
    ThinkingBackchannelPlacementError,
)
from pipecat_thinking_backchannel.plugin import ThinkingBackchannel
from tests.conftest import MemoryCache


class FakeSTT(STTService):
    async def run_stt(self, audio: bytes):
        yield None


class FakeLLM(LLMService):
    async def _process_gen(self, context):
        yield None


class FakeTTSOut(BaseOutputTransport):
    pass


def _pipeline_pieces():
    stt = FakeSTT()
    llm = FakeLLM(settings=None)
    from pipecat.services.tts_service import TTSService

    class FakeTTS(TTSService):
        async def run_tts(self, text: str, context_id: str):
            yield None

    tts = FakeTTS()
    out = FakeTTSOut(TransportParams())
    return stt, llm, tts, out


async def test_inserted_after_tts_in_order():
    thinking = ThinkingBackchannel(cache=MemoryCache(prefilled=True))
    stt, llm, tts, out = _pipeline_pieces()
    base = [BaseInputTransport(TransportParams()), stt, llm, tts, out]
    placed = thinking(base)

    assert placed[0] is base[0]
    # [input, stt, llm, tts, recorder, gate, player, output]
    assert placed[4].__class__.__name__ == "ClipRecorder"
    assert placed[5].__class__.__name__ == "ThinkingBackchannelProcessor"
    assert placed[6].__class__.__name__ == "ClipPlayer"
    assert placed[7] is out
    assert len(placed) == 8


async def test_inserted_before_output_when_no_tts():
    thinking = ThinkingBackchannel(cache=MemoryCache(prefilled=True))
    stt, llm, _, out = _pipeline_pieces()
    placed = thinking([stt, llm, out])

    # No TTS: [stt, llm, recorder, gate, player, output]
    assert placed[2].__class__.__name__ == "ClipRecorder"
    assert placed[5] is out


async def test_raises_without_anchor():
    thinking = ThinkingBackchannel(cache=MemoryCache(prefilled=True))
    llm = FakeLLM(settings=None)
    with pytest.raises(ThinkingBackchannelPlacementError):
        thinking([llm])


async def test_library_is_shared_between_gate_and_player():
    thinking = ThinkingBackchannel(cache=MemoryCache(prefilled=True))
    _, _, tts, out = _pipeline_pieces()
    placed = thinking([tts, out])

    gate = placed[2]
    player = placed[3]
    assert gate._clips is thinking.clips
    assert player._clips is thinking.clips
