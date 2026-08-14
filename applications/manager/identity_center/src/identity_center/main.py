"""进程入口：等价于 `uvicorn identity_center.app:app`。"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from identity_center.infrastructure.config import settings

    uvicorn.run(
        "identity_center.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
