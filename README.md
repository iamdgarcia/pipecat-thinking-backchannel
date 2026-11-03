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
[![Pipecat](https://img.shields.io/badge/Pipecat-1.15.1-brightgreen)](https://github.com/pipecat-ai/pipecat)

Your Pipecat agent talks while it works: "hmm", "one sec", "let me check"
while it waits for the LLM's first token or for a tool to finish.

The companion to [`pipecat-backchannel`](https://github.com/maisterr/pipecat-backchannel),
which makes the bot sound like it's *listening* while the user talks. This one
makes it sound like it's *working* while it thinks.

## Demo

https://github.com/user-attachments/assets/8a999539-1158-4358-b2fe-f8f4ac71ab5c

The bot says "let me check" while the tool runs, and picks the conversation back up when the reply starts.

## Why

Ask a voice agent something that needs a tool and count the silences: one
while the model decides to call the tool, one while the tool runs, one more
for every extra iteration, and a final one for the reply's first token. An
agent that calls three tools before answering can leave the user listening to
nothing for tens of seconds, spread over five gaps. Silence while someone is
"working on it" reads as a hang, so the user repeats the question or hangs up.

People already send these fillers by hand — pipecat's own function-calling
example queues a fixed `TTSSpeakFrame("Let me check on that.")` from an event
handler. That works, and it has three problems: one fixed phrase every time,
nothing for the first-token wait, and the filler lands in the LLM context as
if the bot had meant to say it.

This library does it inside the framework: fillers in the bot's own voice,
into every kind of working silence, remembered by no one. Like its sibling, it
**takes no turn** — clips play with `append_to_context=False`, so the
conversation history has no record that anything happened.

The gate is a pure frame state machine — no VAD, no turn model, no ONNX, no
per-call LLM cost:

| Frame (downstream of TTS)                               | Meaning               | Gate action               |
| ------------------------------------------------------- | --------------------- | ------------------------- |
| `LLMFullResponseStartFrame`                             | reply being generated | arm the first-token timer |
| `FunctionCallInProgressFrame`                           | tool started          | arm the tool timer        |
| `AggregatedTextFrame` / `TTSAudioRawFrame` / `TTSStartedFrame` | real reply is flowing | disarm everything  |
| `FunctionCallResultFrame`                               | tool finished         | disarm the tool timer     |
| `LLMFullResponseEndFrame`                               | reply complete        | disarm everything         |
| `InterruptionFrame` (ex `StartInterruptionFrame`)       | user barged in        | disarm everything         |

## Install

```bash
uv add pipecat-thinking-backchannel
```

## Usage

```python
from pipecat_thinking_backchannel import ThinkingBackchannel

thinking = ThinkingBackchannel()  # one per process, not per session

pipeline = Pipeline(thinking([
    transport.input(),
    stt,
    context_aggregator.user(),
    llm,          # with function-calling tools
    tts,
    transport.output(),
    context_aggregator.assistant(),
]))
```

One wrapper call is the whole setup. It inserts everything directly
downstream of your TTS service (the recorder needs the TTS's raw output, the
gate needs the TTS's frames to know what the bot is doing, and the player
must be in the audio path — one position serves all three, and placement is
automatic).

The first run records the clips through **your own TTS**, so they come out in
your bot's voice, and caches them in `.clip_cache/`. Every later run starts
from the cache. Delete that directory after changing voice.

## Record before the first call

The first session waits a few seconds while the recorder fills the clip
cache. Move that wait to app startup so the first caller never pays it:

```python
thinking = ThinkingBackchannel()     # one per process

async def run_bot(...):
    pipeline = Pipeline(thinking([..., llm, tts, ...]))

if __name__ == "__main__":
    asyncio.run(thinking.prewarm(tts=make_tts()))   # throwaway TTS instance
    main()
```

## Tuning

```python
ThinkingBackchannel(
    volume=1.0,                          # how loud, relative to the recording
    params=ThinkingParams(
        llm_delay_s=1.0,                 # first-token wait before speaking up
        tool_delay_s=0.8,                # running-tool wait before speaking up
        fire_probability=0.9,            # chance of speaking up at a good moment
        cooldown_s=4.0,                  # minimum gap between fillers
        max_per_episode=2,               # most fillers per reply, however long
    ),
)
```

`fire_probability` and `cooldown_s` set how chatty the bot feels while it
works. Keep some slack: a bot that narrates every single gap sounds
mechanical, because people explaining something don't comment on every pause
either.

`volume` defaults to 1.0 — unlike the user-side backchannel, which sits under
someone else's speech at 0.6, here the bot has the floor and the silence.

Set `loguru` to `DEBUG` to see each fire/skip decision and its reason.

### Episode budget

`max_per_episode` counts from the moment the bot starts working on a reply to
the moment real speech flows or the reply ends. A ten-tool agent says at most
that many fillers for the whole run; a user interruption starts a fresh
episode.

### Going quiet mid-session

Some stretches of a call want no narration: the bot reading out a card number
field by field, or a supervisor flow that drives the replies itself.

The component that decides when to fire is the `ThinkingBackchannelProcessor`,
one per pipeline. `thinking([...])` returns your processor list with it
inserted, so keep that list, build the `Pipeline` from it, and pull the gate
out of it:

```python
from pipecat_thinking_backchannel import ThinkingBackchannelProcessor

processors = thinking([transport.input(), stt, llm, tts, transport.output()])
pipeline = Pipeline(processors)

gate = next(p for p in processors if isinstance(p, ThinkingBackchannelProcessor))
gate.enabled = False   # stop firing; every frame still passes through
...
gate.enabled = True    # resume
```

`enabled` is a plain attribute, safe to set at any moment.

## Custom clips

The default inventory is English. Fillers are just text recorded through your
TTS, so any language the TTS speaks works — the gate itself never looks at
words. For a Spanish pipeline:

```python
ThinkingBackchannel(clip_groups={
    "thinking": ["Mmm.", "Déjame pensar.", "A ver.", "Vale..."],
    "working": ["Un momento.", "Déjame verificar.", "Ahora te digo.", "Espere un segundo."],
})
```

Two rules for a custom inventory:

- **Give each group at least two clips.** A one-clip group repeats itself.
- **Get variety from punctuation, and keep the vocabulary small.** `thinking`
  is the sound of someone composing a thought (short hesitations), `working`
  the sound of someone doing something and coming right back. A long phrase
  like "I'm checking that for you right now" stops being a filler and becomes
  a reply. Punctuation is the handle your TTS gives you: `"Mmm."` falls,
  `"Mmm..."` draws out, `"Vale,"` stays up.

Pass `cache=` to supply your own pre-recorded `.wav` files instead of
recording through the pipeline's TTS.

## Run the example

```bash
cp examples/.env.template examples/.env   # OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY
uv run examples/bot.py --transport webrtc
```

Open the WebRTC client, ask something that needs both tools ("what's the
weather in Madrid, and find me ramen there"), and listen: a filler lands when
the model is deciding and another while each tool runs, and the final answer
speaks over nothing.

## Limitations

- **Standard LLM pipelines only.** The gate watches frames emitted by
  pipecat's LLM services (`LLMFullResponseStartFrame`, function-call frames).
  Speech-to-speech pipelines don't emit them, so there is nothing to gate on.
- **Silences only, after speech.** Frames arming the gate are queued behind
  any audio still playing by the TTS service, so fillers cover quiet gaps —
  a tool call that starts while the bot is mid-sentence gets no filler until
  the sentence drains. Covering speech itself is the other plugin's problem.
- **Canned prosody.** The clips are fixed recordings chosen at random within
  their group; the bot cannot react to what a tool just returned. Fillers are
  deliberately generic ("one sec", never "checking the weather").

## Roadmap

- [ ] **Learn per-tool patience.** Tools that always take 5s don't need a
      filler at 0.8s; tools that usually take 1s do. Remember recent
      durations per tool name and bias `tool_delay_s` from them.
- [ ] **Smarter selection.** Let a custom selector see the function name and
      the running iteration count, so "let me check" pairs with lookup tools
      and "hmm" with a long first-token wait.
- [ ] **Fire on async results.** `run_llm=False` results arrive while the
      bot may be silent in other ways; a filler that acknowledges "found
      something" without speaking its content.

## Compatibility

Tested with Pipecat v1.15.1 (works with `pipecat-ai>=1.6.0,<2`; the gate
handles the `StartInterruptionFrame` → `InterruptionFrame` rename). Requires
[`pipecat-backchannel`](https://github.com/maisterr/pipecat-backchannel)
0.3.x, which carries the clip library, recorder, player and prewarm machinery
this package reuses.

## Attribution

Clip infrastructure by [pipecat-backchannel](https://github.com/maisterr/pipecat-backchannel)
© Taras Maister (BSD-2-Clause). This package adds the frame-driven gate for
the bot's side of the conversation. Not affiliated with Daily or the Pipecat
core team.

## License

BSD 2-Clause © Daniel Garcia Pena, matching Pipecat's own license.
