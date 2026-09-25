"""Serve StagePulse stage audio and captions locally."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from stagepulse.manager import StageManager
from stagepulse.web import create_app


ROOT = Path(__file__).resolve().parents[1]


def load_server_config(env_file: Path) -> tuple[str | None, str | None]:
    values = dotenv_values(env_file)
    api_key = values.get("GEMINI_API_KEY")
    public_base_url = os.environ.get(
        "STAGEPULSE_PUBLIC_BASE_URL", values.get("STAGEPULSE_PUBLIC_BASE_URL")
    )
    return api_key, public_base_url


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
    api_key, public_base_url = load_server_config(ROOT / ".env")
    if not api_key:
        parser.error("GEMINI_API_KEY is missing from the project .env file")
    manager = StageManager.from_file(
        args.config, api_key, debug_reconnect_after=args.debug_reconnect_after
    )
    uvicorn.run(
        create_app(manager, public_base_url=public_base_url),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
