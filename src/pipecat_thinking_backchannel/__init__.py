"""pipecat-thinking-backchannel: fillers while the bot thinks or runs tools.

The companion to ``pipecat-backchannel`` (which backchannels while the *user*
speaks). This one plays short fillers ("hmm", "one sec", "let me check") into
the silences an agentic pipeline leaves while the LLM works out its reply and
runs its tools.
"""

from pipecat_thinking_backchannel.clips import DEFAULT_CLIP_GROUPS
from pipecat_thinking_backchannel.gate import (
    ThinkingBackchannelProcessor,
    ThinkingParams,
)
from pipecat_thinking_backchannel.placement import (
    ThinkingBackchannelPlacementError,
    insert_after_tts,
)
from pipecat_thinking_backchannel.plugin import ThinkingBackchannel

__all__ = [
    "DEFAULT_CLIP_GROUPS",
    "ThinkingBackchannel",
    "ThinkingBackchannelPlacementError",
    "ThinkingBackchannelProcessor",
    "ThinkingParams",
    "insert_after_tts",
]
