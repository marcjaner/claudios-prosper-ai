import os

import uvicorn
from dotenv import load_dotenv

HOST = "0.0.0.0"
PORT = 7860


def main() -> None:
    print("[local STT] Loading .env…", flush=True)
    load_dotenv()
    if not os.getenv("DEEPGRAM_API_KEY"):
        raise SystemExit(
            "[local STT] DEEPGRAM_API_KEY is missing. Add it to .env, then try again."
        )

    print("[local STT] Loading Pipecat and Deepgram…", flush=True)
    from .server import app

    print("[local STT] Starting server…", flush=True)
    print(f"[local STT] Open http://localhost:{PORT} after Uvicorn says it is running.", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
