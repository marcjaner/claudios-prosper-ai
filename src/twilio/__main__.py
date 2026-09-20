import uvicorn

from observability.api import mount_dashboard

from .echo import build_echo_agent
from .server import create_app

PORT = 7860

if __name__ == "__main__":
    app = create_app(build_echo_agent)
    mount_dashboard(app)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
