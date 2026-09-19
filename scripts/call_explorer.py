"""Optional standalone launcher for the call explorer."""

import os

import uvicorn

from observability.call_explorer import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CALL_EXPLORER_PORT", "7861")))
