import os
import socket

import uvicorn
from dotenv import load_dotenv

HOST = "0.0.0.0"
DEFAULT_PORT = 7860
CONDUCTOR_PORT_COUNT = 10


def resolve_port() -> int:
    if configured_port := os.getenv("PORT"):
        return int(configured_port)

    first_port = int(os.getenv("CONDUCTOR_PORT", DEFAULT_PORT))
    for port in range(first_port, first_port + CONDUCTOR_PORT_COUNT):
        if is_port_available(port):
            return port
    raise RuntimeError(f"No free port found from {first_port} to {port}")


def is_port_available(port: int) -> bool:
    with socket.socket() as candidate:
        try:
            candidate.bind((HOST, port))
        except OSError:
            return False
    return True


def main() -> None:
    print("[local STT] Loading .env…", flush=True)
    load_dotenv()
    if not os.getenv("DEEPGRAM_API_KEY"):
        raise SystemExit(
            "[local STT] DEEPGRAM_API_KEY is missing. Add it to .env, then try again."
        )

    print("[local STT] Loading Pipecat and Deepgram…", flush=True)
    from .server import app

    port = resolve_port()
    print("[local STT] Starting server…", flush=True)
    print(
        f"[local STT] Open http://localhost:{port} after Uvicorn says it is running.",
        flush=True,
    )
    uvicorn.run(app, host=HOST, port=port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
