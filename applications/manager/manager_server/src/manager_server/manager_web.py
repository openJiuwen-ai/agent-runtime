"""Serve Manager Web via FastAPI (static dist + HTTP/SSE reverse proxies)."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding", "connection"})


def _manager_web_dist() -> Path:
    return Path(__file__).resolve().parents[2].parent / "manager_web" / "dist"


def _user_web_dist() -> Path:
    return Path(__file__).resolve().parents[4] / "access" / "user_web" / "dist"


def _coerce_backend_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"backend url must be http/https: {raw}")
    return url


async def _relay(
    request: Request, upstream_url: str, tag: str, *, timeout: float = 30.0
) -> Response:
    """反向代理一个请求到 upstream_url（本机目标，trust_env=False 不读环境代理）。"""
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"
    outbound_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _SKIP_REQ_HEADERS
    }
    payload = await request.body()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            upstream = await client.request(
                request.method, upstream_url, content=payload, headers=outbound_headers,
            )
    except httpx.HTTPError as exc:
        logging.getLogger("jiuwenclaw-manager-web").error("%s relay failed: %s", tag, exc)
        return Response(content=f"{tag} relay failed".encode(), status_code=502)
    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _SKIP_RESP_HEADERS
    }
    return Response(
        content=upstream.content, status_code=upstream.status_code, headers=response_headers,
    )


async def _relay_stream(request: Request, upstream_url: str, tag: str) -> Response:
    """流式转发一个 HTTP 请求，不缓冲 SSE 响应体。"""
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"
    outbound_headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _SKIP_REQ_HEADERS
    }
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=10.0), trust_env=False
    )
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                upstream_url,
                content=await request.body(),
                headers=outbound_headers,
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        logging.getLogger("jiuwenclaw-manager-web").error(
            "%s relay failed: %s", tag, exc
        )
        return Response(content=f"{tag} relay failed".encode(), status_code=502)

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _SKIP_RESP_HEADERS
    }

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def _authorize_web_request(request: Request, idp_url: str) -> Response | None:
    """通过身份中心校验聊天请求携带的 Bearer token。"""
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("bearer "):
        return Response(
            content=b'{"detail":"Not authenticated"}',
            status_code=401,
            media_type="application/json",
        )
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            upstream = await client.get(
                f"{idp_url}/v1/auth/me",
                headers={"authorization": authorization},
            )
    except httpx.HTTPError as exc:
        logging.getLogger("jiuwenclaw-manager-web").error(
            "identity validation failed: %s", exc
        )
        return Response(
            content=b'{"detail":"identity service unavailable"}',
            status_code=502,
            media_type="application/json",
        )
    if upstream.status_code != 200:
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    return None


def create_manager_web_app(
    dist_root: Path, backend_url: str, idp_url: str,
    *, chat_dist_root: Path | None = None, user_server_url: str = "",
    gateway_sse: str = "",
) -> FastAPI:
    application = FastAPI(title="jiuwenclaw-manager-web", docs_url=None, redoc_url=None)

    # User Web 是统一外壳中的同源 iframe 静态应用，构建 base 固定为 /chat/。
    # 这里直接发 dist，避免代理 Vite HTML 后其 /src、/@vite/client 落到 Manager SPA。
    if chat_dist_root is not None:
        chat_root = chat_dist_root.resolve()
        chat_index = chat_root / "index.html"

        @application.get("/chat")
        async def chat_slash() -> Response:
            return RedirectResponse("/chat/", status_code=307)

        @application.get("/chat/{tail:path}")
        async def chat_static(request: Request, tail: str) -> Response:
            if (
                tail in ("", "index.html")
                and request.headers.get("sec-fetch-dest", "").lower() == "document"
            ):
                return RedirectResponse("/auth", status_code=302)
            candidate = (chat_root / tail).resolve()
            if (
                tail
                and candidate.is_relative_to(chat_root)
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            return FileResponse(chat_index)

    if user_server_url:
        # iframe 的文件接口仍由 User Server 提供，统一入口只做同源转发。
        @application.api_route(
            "/file-api/{tail:path}",
            methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
        )
        async def relay_file_api(request: Request, tail: str) -> Response:
            return await _relay(
                request,
                f"{user_server_url}/file-api/{tail}",
                "file-api",
                timeout=300.0,
            )

    # chat iframe 内的 user_web 使用同源 HTTP POST + SSE。
    if gateway_sse:
        @application.post("/web/invoke")
        async def relay_gateway_sse(request: Request) -> Response:
            auth_error = await _authorize_web_request(request, idp_url)
            if auth_error is not None:
                return auth_error
            return await _relay_stream(request, gateway_sse, "gateway-sse")

    @application.api_route(
        "/api/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_manager_api(request: Request, tail: str) -> Response:
        # 平台管理 API → 本机 Manager API(8765)。
        return await _relay(request, f"{backend_url}/api/{tail}", "api")

    @application.api_route(
        "/idp/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_idp_api(request: Request, tail: str) -> Response:
        # 认证/目录 API → 独立认证服务(jiuwenclaw_identity, 8770)。单源避免 CORS。
        return await _relay(request, f"{idp_url}/{tail}", "idp")

    @application.middleware("http")
    async def static_cache_control(request: Request, call_next) -> Response:
        """HTML 可缓存但须校验（未发版刷新走 304）；/assets/ 带 hash 长期缓存。"""
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif request.url.path.startswith(("/assets/", "/chat/assets/")):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        return response

    # SPA history 路由回退:存在的静态文件直接发,其余路径(/auth、/manager/*、/user/* 等
    # 深链刷新)回退 index.html,交给前端路由(生产由 nginx try_files 承担,这里给构建版兜底)。
    _index = dist_root / "index.html"

    @application.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> Response:
        candidate = (dist_root / full_path).resolve()
        if (
            full_path
            and str(candidate).startswith(str(dist_root.resolve()))
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(_index)

    return application


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve JiuwenClaw Manager Web static files.")
    parser.add_argument("--host", default=os.getenv("MANAGER_WEB_HOST", "localhost"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MANAGER_WEB_PORT", "5273")),
    )
    parser.add_argument("--dist", default=str(_manager_web_dist()))
    parser.add_argument(
        "--proxy-target",
        default=os.getenv("MANAGER_WEB_PROXY_TARGET", "http://127.0.0.1:8765"),
        help="Claw Manager REST base URL for /api relay.",
    )
    parser.add_argument(
        "--idp-target",
        default=os.getenv("MANAGER_WEB_IDP_TARGET", "http://127.0.0.1:8770"),
        help="Identity service base URL for /idp relay.",
    )
    parser.add_argument("--chat-dist", default=str(_user_web_dist()))
    parser.add_argument(
        "--user-server-target",
        default=os.getenv("MANAGER_WEB_USER_SERVER_TARGET", "http://127.0.0.1:5174"),
        help="User Server base URL for /file-api relay.",
    )
    parser.add_argument(
        "--gateway-sse",
        default=os.getenv(
            "MANAGER_WEB_GATEWAY_SSE",
            "http://127.0.0.1:19001/web/invoke",
        ),
        help="Gateway HTTP/SSE URL for /web/invoke relay.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("MANAGER_WEB_LOG_LEVEL", "info"),
    )
    args = parser.parse_args()

    dist_root = Path(args.dist).expanduser().resolve()
    if not dist_root.is_dir():
        raise SystemExit(f"dist directory not found: {dist_root}")
    chat_dist_root = Path(args.chat_dist).expanduser().resolve()
    if not chat_dist_root.is_dir():
        raise SystemExit(f"chat dist directory not found: {chat_dist_root}")

    try:
        backend_url = _coerce_backend_url(args.proxy_target)
        idp_url = _coerce_backend_url(args.idp_target)
        user_server_url = _coerce_backend_url(args.user_server_target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    log = logging.getLogger("jiuwenclaw-manager-web")
    log.info("serving %s", dist_root)
    log.info("http://%s:%s", args.host, args.port)
    log.info("/api relay -> %s", backend_url)
    log.info("/idp relay -> %s", idp_url)
    log.info("/chat static -> %s", chat_dist_root)
    log.info("/file-api relay -> %s", user_server_url)
    log.info("/web/invoke relay -> %s", args.gateway_sse)

    app = create_manager_web_app(
        dist_root, backend_url, idp_url,
        chat_dist_root=chat_dist_root,
        user_server_url=user_server_url,
        gateway_sse=args.gateway_sse,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
