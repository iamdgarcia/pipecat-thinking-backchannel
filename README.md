<div align="center">
  <a href="https://github.com/pipecat-ai/pipecat">
    <img alt="Pipecat" width="220px" height="auto" src="https://raw.githubusercontent.com/pipecat-ai/pipecat/main/pipecat.png">
  </a>
  <p><em>A community integration for <a href="https://github.com/pipecat-ai/pipecat">Pipecat</a></em></p>
</div>

# pipecat-thinking-backchannel

[![PyPI](https://img.shields.io/pypi/v/pipecat-thinking-backchannel?cacheSeconds=3600)](https://pypi.org/project/pipecat-thinking-backchannel)
[![Python](https://img.shields.io/pypi/pyversions/pipecat-thinking-backchannel)](https://pypi.org/project/pipecat-thinking-backchannel)
[![License](https://img.shields.io/badge/license-BSD--2--Clause-blue)](LICENSE)
[![Pipecat](https://img.shields.io/Pipecat-1.15.1-brightgreen)](https://github.com/pipecat-ai/pipecat)

A signal processor that knows when your Pipecat agent has gone quiet at work:
the first-token wait, or a tool that's taking a while.

## Why

Ask a voice agent something that needs a tool and count the silences: one
while the model decides to call the tool, one while the tool runs, one more
for every extra iteration, and a final one for the reply's first token. An
agent that calls three tools before answering can leave the user listening to
nothing for tens of seconds, spread over five gaps. Silence while someone is
"working on it" reads as a hang, so the user repeats the question or hangs up.

The fix is two decisions, and pipecat's pipelines are clearest when they stay
separate. **When** is the bot quietly working? That's a frame state machine:
this package's one job. **What** to say about it? That's yours: a fixed
phrase, a rotating list, a phrase per tool, silence. The processor fires
`on_bot_working_idle(kind, function_name)`; you handle the event — the same
signal/action split as pipecat's `UserIdleController` on the user side.

Because the default action speaks through your real TTS (`TTSSpeakFrame` with
`append_to_context=False`), the filler is a real bot utterance: turn tracking,
RTVI events, interruptions and mute strategies all behave exactly as they do
for any other reply, and the filler never reaches the LLM context.

## Install

```bash
uv add pipecat-thinking-backchannel
```

## Usage

Place the processor between the LLM and the TTS, and wire the event:

```python
from pipecat_thinking_backchannel import BotWorkingIdleProcessor, speak_filler

idle = BotWorkingIdleProcessor()
idle.add_event_handler("on_bot_working_idle", speak_filler(tts))

pipeline = Pipeline([
    transport.input(),
    stt,
    context_aggregator.user(),
    llm,
    idle,                       # between the LLM and the TTS
    tts,
    transport.output(),
    context_aggregator.assistant(),
])
```

`speak_filler(tts)` is the default action: it picks a phrase by kind
(`"llm"` for the first-token wait, `"tool"` for tool waits) and queues it as a
`TTSSpeakFrame(phrase, append_to_context=False)` on your TTS. Bring your own
words:

```python
from pipecat_thinking_backchannel import speak_filler

idle.add_event_handler(
    "on_bot_working_idle",
    speak_filler(tts, {
        "llm": ("Let me think.", "Good question."),
        "tool": ("Let me check on that.", "One moment.", "Looking into it."),
    }),
)
```

And because the event carries `function_name`, per-tool phrases are a small
handler away:

```python
import random

@idle.event_handler("on_bot_working_idle")
async def on_bot_working_idle(processor, kind, function_name=None):
    if function_name == "get_weather":
        phrase = "Checking the sky real quick."
    else:
        phrase = random.choice(("One moment.", "Let me check on that."))
    await tts.queue_frame(TTSSpeakFrame(phrase, append_to_context=False))
```

### The dials

```python
from pipecat_thinking_backchannel import BotWorkingIdleProcessor, BotWorkingIdleParams

idle = BotWorkingIdleProcessor(params=BotWorkingIdleParams(
    llm_delay_s=1.0,       # quiet this long waiting for the first token
    tool_delay_s=0.8,      # quiet this long waiting on a tool
    cooldown_s=4.0,        # minimum time between events
    fire_probability=0.9,  # below 1.0, or it sounds mechanical
    max_per_episode=2,     # most events per reply (from first arm to reply end)
))
```

A wait that expires while the bot is still speaking is held, and fires the
moment the bot actually goes quiet: the filler belongs to the silence, not to
the sentence it would have talked over.

## How it works

No VAD, no turn model, no ONNX — a frame state machine with one timer.

- **Downstream** (from the LLM): the response start arms the first-token wait;
  each `FunctionCallInProgressFrame` arms the tool wait, refreshed per call
  (parallel batches re-arm, never stack); real text, call results, the reply's
  end or an interruption disarm everything.
- **Upstream** (from the transport): `BotStarted/StoppedSpeakingFrame` — the
  ground truth of the bot's mouth. Nothing fires while the bot is speaking.

The event is the whole API. What the app does with it — speak, play a clip,
update a UI, log — is the app's decision, which is exactly where per-tool
phrases and learned tool durations belong as they grow.

## Run the example

```bash
cp examples/.env.template examples/.env   # add your keys
uv run examples/bot.py --transport webrtc
```

A support agent whose routine lines repeat; call it twice and hear the second
call come alive on the same phrases. The stats log at connect/disconnect shows
what the signal caught.

## Compatibility

Tested with Pipecat v1.15.1.

## Limitations

- **Pipeline agents only.** Speech-to-speech models don't emit the frames the
  signal watches, so there is nothing to observe.
- **Exact phrasing is on the app.** The signal knows the wait's shape, not the
  words; the fillers are whatever you queue.

## Roadmap

- Per-tool patience: longer `tool_delay_s` for tools known to be slow, learned
  from measured durations — as logic in a handler this is now app-side.
- An optional clip-based action for pre-recorded fillers, built on the real
  TTS frame signature (kept out of this package until the fake-output concern
  has a clean answer).

## Credits

- [Echo (echospeaks.ai)](https://www.echospeaks.ai/) — same problem space from
  the cost side.
- [pipecat-backchannel](https://github.com/maisterr/pipecat-backchannel) — the
  user-side companion: it makes the bot sound like it's *listening* while the
  user talks.
