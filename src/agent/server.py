from twilio import create_app

from .transcription import INITIAL_GREETING, create_transcription_agent

app = create_app(create_transcription_agent(), initial_greeting=INITIAL_GREETING)
