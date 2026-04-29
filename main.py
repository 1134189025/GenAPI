from __future__ import annotations

import argparse
import os

import uvicorn
from api import create_app

app = create_app()


def _default_port() -> int:
    try:
        return int(str(os.getenv("GENAPI_PORT") or "8000").strip())
    except ValueError:
        return 8000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Genapi API server.")
    parser.add_argument("--version", action="store_true", help="print the Genapi version and exit")
    parser.add_argument("--host", default=os.getenv("GENAPI_HOST", "127.0.0.1"), help="bind host")
    parser.add_argument("--port", type=int, default=_default_port(), help="bind port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(app.version)
        return 0
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
