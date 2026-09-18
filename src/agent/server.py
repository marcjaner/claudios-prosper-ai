from twilio import create_app

from .transcription import create_transcription_agent

# Placeholder until the LLM exists: proves our speech reaches the caller.
REPLY = "Hola, ha llamado a la Clínica Arenal. ¿En qué puedo ayudarle?"

app = create_app(create_transcription_agent(reply=REPLY))
