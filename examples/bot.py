#
# SPDX-License-Identifier: MIT
#

"""An agentic bot that talks while it works.

The manual way to do this is pipecat's own function-calling example:

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        await tts.queue_frame(TTSSpeakFrame("Let me check on that."))

One fixed phrase, only on tool starts, nothing for the first-token wait, and
the filler lands in the LLM context as if the bot had meant to say it. This
example replaces that with ThinkingBackchannel: fillers in the bot's own
voice, on every kind of working silence, remembered by no one.

The tools here deliberately sleep a few seconds so the silences are long
enough to hear the fillers fire into.

Run with: uv run examples/bot.py --transport webrtc
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipecat_thinking_backchannel import ThinkingBackchannel, ThinkingParams

# Relative to this file, not the shell's cwd, so the example runs from anywhere.
load_dotenv()


async def get_weather(params: FunctionCallParams, location: str):
    """Get the current weather for a city.

    Args:
        location (str): The city, e.g. "San Francisco".
    """
    # Slow on purpose: long enough that the tool backchannel has room to fire.
    await asyncio.sleep(4)
    await params.result_callback({"conditions": "sunny", "temperature": "22"})


async def find_restaurant(params: FunctionCallParams, location: str, food: str):
    """Find a restaurant serving a kind of food in a city.

    Args:
        location (str): The city, e.g. "Madrid".
        food (str): The kind of food, e.g. "ramen".
    """
    await asyncio.sleep(3)
    await params.result_callback({"name": "Ramen House", "rating": 4.6})


# One per process, prewarmed below and then called for every session.
thinking = ThinkingBackchannel(
    params=ThinkingParams(llm_delay_s=1.2, tool_delay_s=1.0, cooldown_s=5.0)
)


def make_tts() -> CartesiaTTSService:
    return CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        sample_rate=24000,
        settings=CartesiaTTSService.Settings(
            voice=os.getenv(
                "CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121"
            ),
        ),
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments) -> None:
    logger.info("Starting bot")

    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    tts = make_tts()

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            system_instruction=(
                "You are a helpful assistant in a voice conversation. You have tools "
                "for weather and restaurants; use them whenever they would help, and "
                "chain them when a request needs both. Your responses will be spoken "
                "aloud, so keep them short and conversational."
            ),
        ),
    )

    context = LLMContext(tools=[get_weather, find_restaurant])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        thinking(
            [
                transport.input(),
                stt,
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        context.add_message(
            {
                "role": "developer",
                "content": "Introduce yourself briefly, then ask where the user lives.",
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }

    transport = await create_transport(runner_args, transport_params)

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    # Record the clips before the server accepts a single connection, so no
    # caller ever waits for them. Returns straight away once .clip_cache/ is
    # populated, so this only costs anything on the very first run.
    asyncio.run(thinking.prewarm(tts=make_tts()))

    main()
