"""A support agent that talks while it works — signal from action.

pipecat's own function-calling example handles this manually:

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        await tts.queue_frame(TTSSpeakFrame("Let me check on that."))

One fixed phrase, only on tool starts, nothing for the first-token wait. This
example replaces the hand wiring with the signal processor, and keeps the
action exactly as explicit: the event handler decides what to say.

The signal goes between the LLM and the TTS; the action speaks through the
real TTS with append_to_context=False, so the filler never reaches the context.

Run with: uv run examples/bot.py --transport webrtc
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.cerebras.llm import CerebrasLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner

from pipecat_thinking_backchannel import (
    DEFAULT_PHRASES,
    BotWorkingIdleParams,
    BotWorkingIdleProcessor,
    speak_filler,
)

# Relative to this file, not the shell's cwd, so the example runs from anywhere.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

TOOLS_DELAY_S = 3.0  # slow tools below, so the waits are long enough to hear


async def get_weather(params, location: str):
    """Get the current weather for a city.

    Args:
        location (str): The city, e.g. "San Francisco".
    """
    await asyncio.sleep(TOOLS_DELAY_S)
    await params.result_callback({"conditions": "sunny", "temperature": "22"})


async def find_restaurant(params, location: str, food: str):
    """Find a restaurant serving a kind of food in a city.

    Args:
        location (str): The city, e.g. "Madrid".
        food (str): The kind of food, e.g. "ramen".
    """
    await asyncio.sleep(TOOLS_DELAY_S)
    await params.result_callback({"name": "Ramen House", "rating": 4.6})


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

    llm = CerebrasLLMService(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        model=os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
        settings=CerebrasLLMService.Settings(
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

    # The signal: one processor between the LLM and the TTS.
    idle = BotWorkingIdleProcessor(
        params=BotWorkingIdleParams(llm_delay_s=1.2, tool_delay_s=1.0, cooldown_s=5.0)
    )
    # The action: fillers through the real TTS, out of the LLM context.
    idle.add_event_handler("on_bot_working_idle", speak_filler(tts, DEFAULT_PHRASES))

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            idle,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
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

    main()
