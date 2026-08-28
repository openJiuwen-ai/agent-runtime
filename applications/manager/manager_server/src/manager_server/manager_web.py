"""Serve Manager Web via FastAPI (static dist + HTTP/SSE reverse proxies)."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from websockets.exceptions import ConnectionClosed

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)
_ACCESS_COOKIE = "openjiuwen_access_token"


def _manager_web_dist() -> Path:
    return Path(__file__).resolve().parents[2].parent / "manager_web" / "dist"


def _coerce_backend_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"backend url must be http/https: {raw}")
    return url


def _coerce_ws_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.startswith("http://"):
        return f"ws://{url.removeprefix('http://')}"
    if url.startswith("https://"):
        return f"wss://{url.removeprefix('https://')}"
    if not url.startswith(("ws://", "wss://")):
        raise ValueError(f"websocket backend url must be ws/wss/http/https: {raw}")
    return url


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


async def _relay(
    request: Request,
    upstream_url: str,
    tag: str,
    *,
    timeout: float = 30.0,
    follow_redirects: bool = False,
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
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=follow_redirects,
        ) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                content=payload,
                headers=outbound_headers,
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
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
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
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0), trust_env=False)
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
        logging.getLogger("jiuwenclaw-manager-web").error("%s relay failed: %s", tag, exc)
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


def _request_authorization(
    headers: object,
    cookies: object,
) -> str:
    header_get = getattr(headers, "get", None)
    authorization = str(header_get("authorization") or "").strip() if header_get else ""
    if authorization:
        return authorization
    cookie_get = getattr(cookies, "get", None)
    access_token = str(cookie_get(_ACCESS_COOKIE) or "").strip() if cookie_get else ""
    return f"Bearer {access_token}" if access_token else ""


async def _validate_identity(
    authorization: str,
    idp_url: str,
) -> tuple[int, bytes, str] | None:
    if not authorization.lower().startswith("bearer "):
        return 401, b'{"detail":"Not authenticated"}', "application/json"
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            upstream = await client.get(
                f"{idp_url}/v1/auth/me",
                headers={"authorization": authorization},
            )
    except httpx.HTTPError as exc:
        logging.getLogger("jiuwenclaw-manager-web").error("identity validation failed: %s", exc)
        return 502, b'{"detail":"identity service unavailable"}', "application/json"
    if upstream.status_code != 200:
        return (
            upstream.status_code,
            upstream.content,
            upstream.headers.get("content-type", "application/json"),
        )
    return None


async def _authorize_web_request(request: Request, idp_url: str) -> Response | None:
    """通过身份中心校验统一用户入口请求。"""
    error = await _validate_identity(
        _request_authorization(request.headers, request.cookies),
        idp_url,
    )
    if error is None:
        return None
    status_code, content, media_type = error
    return Response(content=content, status_code=status_code, media_type=media_type)


async def _relay_websocket(
    client_ws: WebSocket,
    upstream_url: str,
    idp_url: str,
) -> None:
    """校验统一登录后，在 Manager Web 与 Gateway 之间双向转发 WebSocket。"""
    auth_error = await _validate_identity(
        _request_authorization(client_ws.headers, client_ws.cookies),
        idp_url,
    )
    if auth_error is not None:
        await client_ws.close(code=4401)
        return

    if client_ws.url.query:
        upstream_url = f"{upstream_url}?{client_ws.url.query}"

    accepted = False
    try:
        async with websockets.connect(
            upstream_url,
            origin=client_ws.headers.get("origin"),
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream_ws:
            await client_ws.accept()
            accepted = True

            async def client_to_gateway() -> None:
                while True:
                    message = await client_ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream_ws.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream_ws.send(message["bytes"])

            async def gateway_to_client() -> None:
                async for message in upstream_ws:
                    if isinstance(message, bytes):
                        await client_ws.send_bytes(message)
                    else:
                        await client_ws.send_text(message)

            tasks = {
                asyncio.create_task(client_to_gateway()),
                asyncio.create_task(gateway_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
    except WebSocketDisconnect:
        return
    except ConnectionClosed:
        return
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("jiuwenclaw-manager-web").error("gateway-ws relay failed: %s", exc)
        if accepted:
            with suppress(RuntimeError):
                await client_ws.close(code=1011)
        else:
            with suppress(RuntimeError):
                await client_ws.close(code=1013)


def create_manager_web_app(
    dist_root: Path,
    backend_url: str,
    idp_url: str,
    *,
    user_web_url: str = "",
    gateway_http_url: str = "",
    gateway_ws_url: str = "",
) -> FastAPI:
    application = FastAPI(title="jiuwenclaw-manager-web", docs_url=None, redoc_url=None)

    # User Web 由独立服务提供，Manager Web 只保留同源 /chat 入口。
    if user_web_url:

        @application.get("/chat")
        async def chat_slash() -> Response:
            return RedirectResponse("/chat/", status_code=307)

        @application.api_route(
            "/chat/{tail:path}",
            methods=["GET", "HEAD", "OPTIONS"],
        )
        async def chat_static(request: Request, tail: str) -> Response:
            if (
                tail in ("", "index.html")
                and request.headers.get("sec-fetch-dest", "").lower() == "document"
            ):
                return RedirectResponse("/auth", status_code=302)
            return await _relay(
                request,
                _join_url(user_web_url, tail),
                "user-web",
                timeout=300.0,
            )

    if gateway_http_url:

        @application.api_route(
            "/file-api/{tail:path}",
            methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
        )
        async def relay_file_api(request: Request, tail: str) -> Response:
            auth_error = await _authorize_web_request(request, idp_url)
            if auth_error is not None:
                return auth_error
            return await _relay(
                request, _join_url(gateway_http_url, f"file-api/{tail}"), "file-api", timeout=300.0
            )

        @application.api_route(
            "/share-api/{tail:path}",
            methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
        )
        async def relay_share_api(request: Request, tail: str) -> Response:
            auth_error = await _authorize_web_request(request, idp_url)
            if auth_error is not None:
                return auth_error
            return await _relay(
                request,
                _join_url(gateway_http_url, f"share-api/{tail}"),
                "share-api",
                timeout=300.0,
            )

        # 独立前缀避免与 Manager Server 的 /api/v1/* 冲突。
        @application.api_route(
            "/gateway-api/{tail:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        )
        async def relay_gateway_http(request: Request, tail: str) -> Response:
            auth_error = await _authorize_web_request(request, idp_url)
            if auth_error is not None:
                return auth_error
            return await _relay_stream(
                request,
                _join_url(gateway_http_url, f"api/{tail}"),
                "gateway-http",
            )

    if gateway_ws_url:

        @application.websocket("/ws")
        async def relay_gateway_ws_root(websocket: WebSocket) -> None:
            await _relay_websocket(
                websocket,
                _join_url(gateway_ws_url, "ws"),
                idp_url,
            )

        @application.websocket("/ws/{tail:path}")
        async def relay_gateway_ws(websocket: WebSocket, tail: str) -> None:
            await _relay_websocket(
                websocket,
                _join_url(gateway_ws_url, f"ws/{tail}"),
                idp_url,
            )

    @application.api_route(
        "/api/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_manager_api(request: Request, tail: str) -> Response:
        # 平台管理 API → 本机 Manager API(8765)。
        # Manager Server 的部分集合路由以斜杠结尾，FastAPI 会对无斜杠请求
        # 返回指向集群内部域名的 307。代理端完成该规范化跳转，避免内部地址
        # 暴露给浏览器后触发 Failed to fetch。
        return await _relay(
            request,
            f"{backend_url}/api/{tail}",
            "api",
            follow_redirects=True,
        )

    @application.api_route(
        "/manager-api/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_embedded_user_manager_api(request: Request, tail: str) -> Response:
        """Relay the embedded User Web's explicit Manager API namespace."""
        return await _relay(
            request,
            f"{backend_url}/api/{tail}",
            "manager-api",
            follow_redirects=True,
        )

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
    from manager_server.infrastructure.config import settings

    parser = argparse.ArgumentParser(description="Serve JiuwenClaw Manager Web static files.")
    parser.add_argument("--host", default=settings.manager_web_host)
    parser.add_argument(
        "--port",
        type=int,
        default=settings.manager_web_port,
    )
    parser.add_argument("--dist", default=str(_manager_web_dist()))
    parser.add_argument(
        "--proxy-target",
        default=settings.manager_web_proxy_target,
        help="Claw Manager REST base URL for /api relay.",
    )
    parser.add_argument(
        "--idp-target",
        default=settings.manager_web_idp_target,
        help="Identity service base URL for /idp relay.",
    )
    parser.add_argument(
        "--user-web-target",
        default=settings.manager_web_user_web_target,
        help="User Web base URL for the same-origin /chat relay.",
    )
    parser.add_argument(
        "--gateway-http-target",
        default=settings.manager_web_gateway_http_target,
        help="Gateway Web HTTP base URL for /gateway-api and file/share relays.",
    )
    parser.add_argument(
        "--gateway-ws-target",
        default=settings.manager_web_gateway_ws_target,
        help="Gateway WebSocket base URL for /ws relay.",
    )
    parser.add_argument(
        "--log-level",
        default=settings.manager_web_log_level,
    )
    args = parser.parse_args()

    dist_root = Path(args.dist).expanduser().resolve()
    if not dist_root.is_dir():
        raise SystemExit(f"dist directory not found: {dist_root}")
    try:
        backend_url = _coerce_backend_url(args.proxy_target)
        idp_url = _coerce_backend_url(args.idp_target)
        user_web_url = _coerce_backend_url(args.user_web_target)
        gateway_http_url = _coerce_backend_url(args.gateway_http_target)
        gateway_ws_url = _coerce_ws_url(args.gateway_ws_target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    log = logging.getLogger("jiuwenclaw-manager-web")
    log.info("serving %s", dist_root)
    log.info("http://%s:%s", args.host, args.port)
    log.info("/api relay -> %s", backend_url)
    log.info("/idp relay -> %s", idp_url)
    log.info("/chat relay -> %s", user_web_url)
    log.info("/gateway-api,/file-api,/share-api relay -> %s", gateway_http_url)
    log.info("/ws relay -> %s", gateway_ws_url)

    app = create_manager_web_app(
        dist_root,
        backend_url,
        idp_url,
        user_web_url=user_web_url,
        gateway_http_url=gateway_http_url,
        gateway_ws_url=gateway_ws_url,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
