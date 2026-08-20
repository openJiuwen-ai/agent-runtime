# coding: utf-8
"""agent-runtime 命令行入口（部署脚本 scripts/deploy.sh 调用）。"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the agent-runtime service")
    parser.add_argument(
        "--mode", choices=("local", "server"), required=True,
        help="local uses fakeredis/SQLite/FakeK8s; server uses real resources",
    )
    parser.add_argument("--env-file", type=Path, help="dotenv file loaded first")
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

    os.environ["AGENT_RUNTIME_MODE"] = args.mode
    if args.host is not None:
        os.environ["OPENJIUWEN_SERVICE_HOST"] = args.host
    if args.port is not None:
        os.environ["OPENJIUWEN_SERVICE_PORT"] = str(args.port)

    logging.basicConfig(
        level=os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from openjiuwen_runtime.service.config import ServiceConfig

    from .config import AgentRuntimeConfig
    from .main import create_app

    settings = ServiceConfig.from_env()
    arc = AgentRuntimeConfig.from_env()
    application = create_app(settings, arc)

    import uvicorn

    uvicorn.run(
        application.asgi,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
