from twilio import create_app

from .graph_api import register_graph_api
from .transcription import create_transcription_agent
from .utils import load_environment

# Serving this module directly with uvicorn is a normal way to start it, and the
# STT and TTS factories read their keys from the environment when a call builds
# its pipeline. Without this, only `python -m agent` ever sees the .env file.
load_environment()

app = create_app(create_transcription_agent())
register_graph_api(app)
