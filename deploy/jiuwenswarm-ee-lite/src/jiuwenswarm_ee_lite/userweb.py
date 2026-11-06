# coding: utf-8
"""用户面占位页（jiuwenswarm-ee-lite 专属组件，非既有代码改动）。

轻量版不含用户面前端（按需求裁剪），但 manager 建实例时对 user_web_host
做 ``GET /`` 可达性探活（config_host_probe._USER_WEB_HEALTH_PATH="/"）。
本进程只回答一个 200 占位页，说明真实用户消息入口是 gateway 的 ``/ws``。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

_INDEX_HTML = """<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>jiuwenswarm-ee-lite</title></head>
<body>
<h1>jiuwenswarm-ee-lite 用户面占位页</h1>
<p>轻量版不包含用户面前端。用户消息入口：gateway WebSocket <code>/ws</code>
（见 README「服务拓扑与端口」）。</p>
</body></html>"""


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    host = os.getenv("USERWEB_HOST", "127.0.0.1")
    port = int(os.getenv("USERWEB_PORT", "19002"))
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
