# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-02

### Added

- `ThinkingBackchannel` facade: one wrapper call inserts the recorder, the
  gate and the player directly downstream of the pipeline's TTS service.
- `ThinkingBackchannelProcessor`: frame-driven gate that plays filler clips
  while the bot waits for the LLM's first token (`LLMFullResponseStartFrame`)
  or for a running tool (`FunctionCallInProgressFrame` →
  `FunctionCallResultFrame`), disarms on real reply audio/text
  (`AggregatedTextFrame`, `TTSAudioRawFrame`, `TTSStartedFrame`), reply end
  (`LLMFullResponseEndFrame`) and user interruption (`InterruptionFrame`,
  with the pre-1.15 `StartInterruptionFrame` name supported).
- `ThinkingParams`: `llm_delay_s`, `tool_delay_s`, `cooldown_s`,
  `fire_probability`, `max_per_episode`.
- Default English clip inventory (`thinking` / `working` groups); any
  language the pipeline's TTS speaks works via `clip_groups`.
- `prewarm()` to record clips at app startup instead of inside the first
  session (machinery reused from `pipecat-backchannel`).
- Agentic example with two deliberately slow tools (`examples/bot.py`).
