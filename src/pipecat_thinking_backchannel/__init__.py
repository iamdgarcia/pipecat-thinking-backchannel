"""pipecat-thinking-backchannel: your agent talks while it works.

Place one processor between the LLM and the TTS; it fires an event whenever the
bot has gone quiet waiting for its first token or for a tool::

    from pipecat_thinking_backchannel import BotWorkingIdleProcessor, speak_filler

    idle = BotWorkingIdleProcessor()
    idle.add_event_handler("on_bot_working_idle", speak_filler(tts))

    pipeline = Pipeline([
        transport.input(), stt, context_aggregator.user(),
        llm, idle, tts, transport.output(), context_aggregator.assistant(),
    ])

See the repository README for the full story.
"""

from .controller import (
    KIND_LLM,
    KIND_TOOL,
    BotWorkingIdleParams,
    BotWorkingIdleProcessor,
)
from .speak import DEFAULT_PHRASES, speak_filler

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_PHRASES",
    "KIND_LLM",
    "KIND_TOOL",
    "BotWorkingIdleParams",
    "BotWorkingIdleProcessor",
    "__version__",
    "speak_filler",
]
