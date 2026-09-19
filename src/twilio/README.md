# Call debug bundles

Set `CALL_RECORDINGS_DIR=outputs/recordings` to write one directory per call:

```text
outputs/recordings/<call_id>/
├── metadata.json
├── timeline.jsonl
├── caller.wav
├── agent.wav
├── mixed.wav
└── stereo.wav
```

The WAV files share one timeline and include silence, so timestamps in
`timeline.jsonl` line up with the recordings. `stereo.wav` puts the caller on
the left and the agent on the right. The timeline records partial and final
transcripts, VAD and turn boundaries, agent speech, interruptions, tool calls,
and pipeline errors.

For pause or barge-in bugs, compare `vad_speech_stopped`,
`caller_turn_stopped`, and `agent_speech_started` against `caller.wav`.
