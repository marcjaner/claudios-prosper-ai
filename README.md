# claudios-prosper-ai

## Deepgram STT smoke test

Put the Deepgram key in `.env` at the repository root:

```dotenv
DEEPGRAM_API_KEY=your-key
```

Then stream a local audio file through Pipecat's Twilio decoder and Deepgram:

```shell
uv run python scripts/stt_smoke.py path/to/audio.wav
```

The smoke test requires `ffmpeg` on `PATH`.
