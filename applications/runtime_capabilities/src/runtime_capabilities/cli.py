"""Command-line launcher for the runtime capabilities application."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Runtime Capabilities application",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "server"),
        required=True,
        help="local uses SQLite/FakeRedis/Fake Kubernetes; server uses real resources",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="dotenv file loaded before the application is created",
    )
    parser.add_argument("--host", help="override OPENJIUWEN_SERVICE_HOST")
    parser.add_argument("--port", type=int, help="override OPENJIUWEN_SERVICE_PORT")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.env_file is not None:
        env_file = args.env_file.expanduser().resolve()
        if not env_file.is_file():
            raise FileNotFoundError(f"environment file does not exist: {env_file}")
        load_dotenv(env_file, override=False)

    os.environ["RUNTIME_CAPABILITIES_MODE"] = args.mode
    if args.host is not None:
        os.environ["OPENJIUWEN_SERVICE_HOST"] = args.host
    if args.port is not None:
        os.environ["OPENJIUWEN_SERVICE_PORT"] = str(args.port)

    from .application import (
        RuntimeCapabilitiesConfig,
        build_service_config,
        create_app,
    )

    config = RuntimeCapabilitiesConfig.from_env()
    service_config = build_service_config(config)
    application = create_app(config)

    import uvicorn

    uvicorn.run(
        application.asgi,
        host=service_config.host,
        port=service_config.port,
        workers=1,
    )


__all__ = ["main"]
