"""Serve StagePulse stage audio and captions locally."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from stagepulse.manager import StageManager
from stagepulse.web import create_app


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/stages.gate3.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--debug-reconnect-after",
        type=float,
        help="Test only: force one provider reconnect after this many seconds",
    )
    args = parser.parse_args()
    api_key = dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if not api_key:
        parser.error("GEMINI_API_KEY is missing from the project .env file")
    manager = StageManager.from_file(
        args.config, api_key, debug_reconnect_after=args.debug_reconnect_after
    )
    uvicorn.run(create_app(manager), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
