"""Serve Manager Web via FastAPI (static dist and management/identity reverse proxies)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)


def _manager_web_dist() -> Path:
    return Path(__file__).resolve().parents[2].parent / "manager_web" / "dist"


def _inject_manager_web_runtime_config(document: str) -> str:
    """Inject deploy-time feature flags into index.html (mirrors User Web)."""
    from manager_server.infrastructure.config import settings

    enabled = bool(settings.workspace_quota_enabled)
    return document.replace(
        "__WORKSPACE_QUOTA_ENABLED_VALUE__",
        "true" if enabled else "false",
    )


def _index_html_response(index_path: Path) -> HTMLResponse:
    body = _inject_manager_web_runtime_config(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content=body,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _coerce_backend_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"backend url must be http/https: {raw}")
    return url


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
        if name.lower() not in _SKIP_REQ_HEADERS | {"x-jiuwen-web-surface"}
    }
    if tag == "idp":
        outbound_headers["X-Jiuwen-Web-Surface"] = "manager"
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


def create_manager_web_app(
    dist_root: Path,
    backend_url: str,
    idp_url: str,
) -> FastAPI:
    application = FastAPI(title="jiuwenclaw-manager-web", docs_url=None, redoc_url=None)

    @application.api_route(
        "/api/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_manager_api(request: Request, tail: str) -> Response:
        if tail == "v1/user-console" or tail.startswith("v1/user-console/"):
            return Response(status_code=404)
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
        "/idp/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_idp_api(request: Request, tail: str) -> Response:
        # 认证/目录 API → 独立认证服务(jiuwenclaw_identity, 8770)。单源避免 CORS。
        return await _relay(request, f"{idp_url}/{tail}", "idp")

    @application.api_route(
        "/{prefix}/{tail:path}", methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def unknown_write(prefix: str, tail: str) -> Response:
        return Response(status_code=404)

    @application.middleware("http")
    async def static_cache_control(request: Request, call_next) -> Response:
        """HTML 可缓存但须校验（未发版刷新走 304）；/assets/ 带 hash 长期缓存。"""
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif request.url.path.startswith("/assets/"):
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
        if full_path.partition("/")[0] in {"chat", "gateway-api", "file-api", "share-api", "ws", "manager-api"}:
            return Response(status_code=404)
        candidate = (dist_root / full_path).resolve()
        if (
            full_path
            and str(candidate).startswith(str(dist_root.resolve()))
            and candidate.is_file()
        ):
            if candidate.name == "index.html":
                return _index_html_response(candidate)
            return FileResponse(candidate)
        return _index_html_response(_index)

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
    for legacy_option in ("--user-web-target", "--gateway-http-target", "--gateway-ws-target"):
        parser.add_argument(legacy_option, help=argparse.SUPPRESS)
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
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    log = logging.getLogger("jiuwenclaw-manager-web")
    log.info("serving %s", dist_root)
    log.info("http://%s:%s", args.host, args.port)
    log.info("/api relay -> %s", backend_url)
    log.info("/idp relay -> %s", idp_url)
    app = create_manager_web_app(dist_root, backend_url, idp_url)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
