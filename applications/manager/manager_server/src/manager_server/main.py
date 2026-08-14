"""进程入口：等价于 `uvicorn manager_server.app:app`。"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from manager_server.infrastructure.config import settings

    uvicorn.run(
        "manager_server.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
