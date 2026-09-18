import uvicorn

from .echo import build_echo_agent
from .server import create_app

PORT = 7860

if __name__ == "__main__":
    uvicorn.run(create_app(build_echo_agent), host="0.0.0.0", port=PORT)
