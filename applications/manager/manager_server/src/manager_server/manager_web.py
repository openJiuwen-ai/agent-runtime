"""Serve Manager Web via FastAPI (static dist and management/identity reverse proxies)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from collections.abc import Awaitable
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)
_ORG_DELETE = re.compile(r"^v1/orgs/([^/]+)/?$")
_ORG_MEMBER_DELETE = re.compile(r"^v1/orgs/([^/]+)/members/([^/]+)/?$")
_USER_DELETE = re.compile(r"^v1/users/([^/]+)/?$")
_USER_ORGS = re.compile(r"^v1/users/([^/]+)/orgs/?$")
_LOG = logging.getLogger("jiuwenclaw-manager-web")
# Cascade tasks outlive the proxied identity response; keep a reference until
# done so the loop cannot garbage-collect a running task.
_RECLAIM_TASKS: set[asyncio.Task[None]] = set()


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


def _reclaim_response(response: httpx.Response, tag: str) -> None:
    """Manager 拒绝级联清理时记录状态码与响应片段，便于定位。"""
    if response.status_code >= 400:
        _LOG.error(
            "%s rejected: status=%s body=%s",
            tag,
            response.status_code,
            response.text[:500],
        )


async def _reclaim_org_teams(
    backend_url: str, group_id: str, authorization: str | None
) -> None:
    """删除组织成功后，让 Manager 停止该组织下的存活团队并释放配额。"""
    url = f"{backend_url}/api/v1/orgs/{quote(group_id, safe='')}/reclaim-teams"
    headers = {"Authorization": authorization} if authorization else {}
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(url, headers=headers)
    except httpx.HTTPError as exc:
        _LOG.error("org team reclaim failed: %s", exc)
        return
    _reclaim_response(response, "org team reclaim")


async def _reclaim_user_teams(
    backend_url: str, user_id: str, authorization: str | None
) -> None:
    """删除用户/移出组织后，让 Manager 停掉该用户已不归属的团队。"""
    url = f"{backend_url}/api/v1/users/{quote(user_id, safe='')}/reclaim-teams"
    headers = {"Authorization": authorization} if authorization else {}
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.post(url, headers=headers)
    except httpx.HTTPError as exc:
        _LOG.error("user team reclaim failed: %s", exc)
        return
    _reclaim_response(response, "user team reclaim")


def _reclaim_done(task: asyncio.Task[None]) -> None:
    """后台级联任务结束时移除引用，并记录未处理异常。"""
    _RECLAIM_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _LOG.error("team reclaim task failed: %s", exc)


def _spawn_reclaim(coro: Awaitable[None]) -> None:
    """Run the cascade after the identity response, not on the /idp request path.

    Each cluster keeps a 60s budget inside Manager, so awaiting the cascade
    would make the browser wait N*60s on an operation that already succeeded --
    and a client timeout would leave the remaining clusters unreclaimed.
    """
    task = asyncio.create_task(coro)
    _RECLAIM_TASKS.add(task)
    task.add_done_callback(_reclaim_done)


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
        response = await _relay(request, f"{idp_url}/{tail}", "idp")
        if not 200 <= response.status_code < 300:
            return response
        authorization = request.headers.get("authorization")
        if request.method == "DELETE":
            matched = _ORG_DELETE.match(tail)
            if matched is not None:
                _spawn_reclaim(
                    _reclaim_org_teams(
                        backend_url, unquote(matched.group(1)), authorization
                    )
                )
            else:
                matched = _ORG_MEMBER_DELETE.match(tail)
                if matched is not None:
                    _spawn_reclaim(
                        _reclaim_user_teams(
                            backend_url, unquote(matched.group(2)), authorization
                        )
                    )
                else:
                    matched = _USER_DELETE.match(tail)
                    if matched is not None:
                        _spawn_reclaim(
                            _reclaim_user_teams(
                                backend_url, unquote(matched.group(1)), authorization
                            )
                        )
        elif request.method == "PUT":
            matched = _USER_ORGS.match(tail)
            if matched is not None:
                _spawn_reclaim(
                    _reclaim_user_teams(
                        backend_url, unquote(matched.group(1)), authorization
                    )
                )
        return response

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
