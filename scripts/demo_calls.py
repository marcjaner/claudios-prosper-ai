import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:7860")
    args = parser.parse_args()
    request = Request(f"{args.url.rstrip('/')}/api/demo/start", method="POST")
    try:
        with urlopen(request) as response:
            body = json.load(response)
    except HTTPError as error:
        raise SystemExit(f"demo start failed: {error.code} {error.reason}") from error
    except URLError as error:
        raise SystemExit(f"demo server unavailable: {error.reason}") from error
    print(f"started {body['started']} demo calls")


if __name__ == "__main__":
    main()
