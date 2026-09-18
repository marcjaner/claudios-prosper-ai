from twilio import create_app

from .transcription import create_transcription_agent

# Placeholder until the LLM exists: proves our speech reaches the caller.
REPLY = "Hello, thank you for calling the Arenal Clinic. How can I help you today?"

app = create_app(create_transcription_agent(reply=REPLY))
