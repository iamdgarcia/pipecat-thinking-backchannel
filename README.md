# pipecat-thinking-backchannel

Make agentic voice AI feel alive — a [Pipecat](https://pipecat.ai) companion to
[`pipecat-backchannel`](https://github.com/maisterr/pipecat-backchannel) that fills the
*bot's* silences, not the user's.

**▶ Try it with your own pipeline:** install, wrap your processor list, done. The clips
are recorded once through your own TTS service and cached, so they come out in your
bot's voice. No LLM calls, no VAD, no turn model.

**Code on GitHub:** https://github.com/iamdgarcia/pipecat-thinking-backchannel

## What it does

`pipecat-backchannel` makes your bot sound like it's listening while the user is still
talking. This package is the mirror image: while the *bot* is working — waiting for the
LLM's first token, or waiting for a tool to run — it plays short fillers ("hmm", "one
sec", "let me check") so the user hears a colleague thinking, not a hung connection.

Agentic setups make those silences long and frequent. An agent that calls three tools
before answering can leave the user listening to nothing for tens of seconds, spread
over several gaps. People have been sending these fillers by hand; this integrates the
behavior into the framework.

The gate is a pure frame state machine — no VAD, no turn model, no ONNX, no per-call
LLM cost:

| Frame (downstream of TTS)                     | Meaning                    | Gate action                |
| --------------------------------------------- | -------------------------- | -------------------------- |
| `LLMFullResponseStartFrame`                   | reply being generated      | arm the first-token timer  |
| `FunctionCallInProgressFrame`                 | tool started               | arm the tool timer         |
| `TTSTextFrame` / `TTSAudioRawFrame` / `TTSStartedFrame` | real reply is flowing | disarm everything   |
| `FunctionCallResultFrame`                     | tool finished              | disarm the tool timer      |
| `LLMFullResponseEndFrame`                     | reply complete             | disarm everything          |
| `InterruptionFrame` (ex `StartInterruptionFrame`) | user barged in         | disarm everything          |

Like its sibling, it **takes no turn**: clips play with `append_to_context=False`, so
the bot's conversation history has no record that anything happened.

## Installation

```bash
uv add pipecat-thinking-backchannel
```

Requires `pipecat-ai>=1.6.0,<2` and `pipecat-backchannel>=0.3` (the clip library,
recorder, player and prewarm machinery are reused from there).

## Usage

```python
from pipecat.pipeline.pipeline import Pipeline
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

Record the clips at application startup so the first caller doesn't wait:

```python
await thinking.prewarm(tts=make_tts())  # throwaway TTS instance
```

The first session records them itself if you skip this (held in startup while it
does). Clips are cached in `.clip_cache/` — delete that directory after changing
voice.

## Configuration

### `ThinkingParams(...)` (passed as `params=`)

| Parameter          | Type  | Default | Description                                                |
| ------------------ | ----- | ------- | ---------------------------------------------------------- |
| `llm_delay_s`      | float | 1.0     | First-token wait before firing. Below this, silence reads as normal turn-taking. |
| `tool_delay_s`     | float | 0.8     | Running-tool wait before firing. Tools are known to be slow, so a little shorter. |
| `cooldown_s`       | float | 4.0     | Minimum time between two clips.                            |
| `fire_probability` | float | 0.9     | Chance of firing at an eligible moment. Below 1.0, or it sounds mechanical. |
| `max_per_episode`  | int   | 2       | Most clips per episode (from the bot starting work to real speech or reply end). A ten-tool agent must not say "one sec" ten times. |

### `clip_groups=`

The clip inventory, grouped by conversational function:

```python
DEFAULT_CLIP_GROUPS = {
    "thinking": ["Hmm.", "Let me think.", "Let me see.", "Right..."],
    "working": ["One sec.", "Let me check.", "Just a moment.", "One moment."],
}
```

`thinking` covers the first-token wait, `working` covers tools. Give each group at
least two clips, or it repeats itself.

### `volume=` (default `1.0`)

Unlike the user-side backchannel — which sits under someone else's speech and defaults
to 0.6 — here the bot has the floor and the silence, so full volume is right.

## Pipeline position

Everything is inserted directly downstream of your TTS service (or immediately before
the output transport if you have no TTS): the recorder needs the TTS's raw output, the
gate needs the TTS's frames to know what the bot is doing, and the player must be in
the audio path. One position serves all three, and placement is automatic.

## Compatibility

Developed against Pipecat 1.15.x; `pipecat-backchannel` 0.3 supports 1.6.0+. Some
pipelines queue frames behind playing audio inside the TTS service, which is exactly
what the gate wants: arming frames arrive when the output goes quiet.

## Attribution

Built on the clip infrastructure of
[`pipecat-backchannel`](https://github.com/maisterr/pipecat-backchannel) by Taras
Maister (BSD-2-Clause). This package adds the frame-driven gate for the bot's side of
the conversation.

## License

MIT — see [LICENSE](LICENSE).
