from observability.api import mount_dashboard
from twilio import create_app

from .graph_api import register_graph_api
from .operator import register_operator_api
from .transcription import INITIAL_GREETING, create_transcription_agent
from .utils import load_environment

# Serving this module directly with uvicorn is a normal way to start it, and the
# STT and TTS factories read their keys from the environment when a call builds
# its pipeline. Without this, only `python -m agent` ever sees the .env file.
load_environment()

app = create_app(create_transcription_agent(), initial_greeting=INITIAL_GREETING)
register_graph_api(app)
register_operator_api(app)
mount_dashboard(app)
