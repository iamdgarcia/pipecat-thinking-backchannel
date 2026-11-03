# Changelog

## 0.2.0

Restructure after maintainer feedback on the docs listing proposal
(pipecat-ai/docs#1636): signal separated from action, mirroring
`UserIdleController`.

**Added**

- `BotWorkingIdleProcessor`: a placeable processor (between the LLM and the
  TTS) that fires `on_bot_working_idle(kind, function_name)`. It watches reply
  and function-call frames downstream and the transport's
  `BotStarted/StoppedSpeakingFrame` upstream, so nothing fires while the bot
  is actually speaking; waits that expire mid-speech are held and fire when
  the silence opens.
- `speak_filler(tts, phrases_by_kind)`: the default action — a handler that
  queues `TTSSpeakFrame(phrase, append_to_context=False)` on the real TTS, so
  fillers are real utterances (turn tracking, RTVI, interruptions) that never
  reach the LLM context.

**Removed**

- The `ThinkingBackchannel` pipeline-rewriting facade: pipelines are wired
  explicitly; the processor is placed by hand now.
- The clip infrastructure and the `pipecat-backchannel` dependency: no
  recorder driving the live TTS, no `ClipPlayer` sending fake TTS output
  frames downstream. The package is signal-only.

## 0.1.0

- `ThinkingBackchannel` facade: fillers in the bot's own voice while it waits
  for the LLM's first token or for tools, played as cached clips with
  `append_to_context=False`.
