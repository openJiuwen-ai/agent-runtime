"""探测模型模板 API Base / Key 是否可达（OpenAI 兼容 ``GET /models``）。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

_DEFAULT_TIMEOUT_SEC = 15.0
_MAX_TIMEOUT_SEC = 30.0
_ANTHROPIC_VERSION = "2023-06-01"
# 与 a2a_discovery.MAX_CARD_BYTES 同量级，避免探测把大响应整包读入内存。
MAX_PROBE_RESPONSE_BYTES = 1_048_576
# 与 a2a_discovery 一致：允许企业内网模型，但拒绝回环 / link-local / 元数据等。
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_BLOCKED_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "anthropic-version",
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
    }
)

# 与用户面 model_error_messages 同类关键字，便于把上游原文收敛成稳定 error_code。
_BALANCE_MARKERS = (
    "insufficient balance",
    "insufficient_balance",
    "insufficient quota",
    "insufficient_quota",
    "payment required",
    "余额不足",
    "欠费",
)
_AUTH_MARKERS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "unauthorized",
    "authentication",
    "access denied",
    "api key 无效",
    "认证失败",
    "鉴权失败",
)
_RATE_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "请求过于频繁",
)


class ProbeTargetBlocked(ValueError):
    """目标地址未通过网络准入（SSRF 防护）。"""


def normalize_model_api_base(api_base: str) -> str:
    """去掉末尾斜杠；若误填完整 chat 路径则剥到基址。"""
    base = str(api_base or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    if base.endswith("/messages"):
        base = base[: -len("/messages")].rstrip("/")
    return base


def models_list_url(api_base: str) -> str:
    return f"{normalize_model_api_base(api_base)}/models"


def _is_anthropic(provider: str) -> bool:
    return str(provider or "").strip().lower() == "anthropic"


def build_probe_headers(
    *,
    api_key: str,
    model_provider: str,
    data: dict[str, Any] | None,
) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    key = str(api_key or "").strip()
    if _is_anthropic(model_provider):
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = _ANTHROPIC_VERSION
    elif key:
        headers["Authorization"] = f"Bearer {key}"

    custom = None
    if isinstance(data, dict):
        custom = data.get("headers")
    if isinstance(custom, dict):
        for raw_name, raw_value in custom.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_value, str):
                continue
            folded = name.lower()
            # 不覆盖探测鉴权头，也不允许改写 Host 等跳过 IP pin 的头。
            if folded in _BLOCKED_REQUEST_HEADERS:
                continue
            headers[name] = raw_value
    return headers


def _model_id_in_payload(payload: Any, model_id: str) -> bool | None:
    """从 ``/models`` 响应里判断是否包含配置的 model_id；无法解析时返回 None。"""
    needle = str(model_id or "").strip()
    if not needle:
        return None
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    found = False
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "").strip() == needle:
            found = True
            break
        if isinstance(row, str) and row.strip() == needle:
            found = True
            break
    return found


def _error_detail_from_bytes(status_code: int, raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")[:300]
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        # UnicodeDecodeError 是 ValueError 子类，只捕父类即可。
        return text or f"HTTP {status_code}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        for key in ("detail", "message"):
            if body.get(key):
                return str(body[key])[:300]
    return text or f"HTTP {status_code}"


def classify_connection_error(
    *,
    status_code: int | None = None,
    detail: str | None = None,
    exc: BaseException | None = None,
) -> str:
    """把探测失败归一成稳定 ``error_code``，供前端按语言渲染。"""
    if isinstance(exc, ProbeTargetBlocked):
        return "target_blocked"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    # InvalidURL 继承 Exception 而非 HTTPError，需单独归类。
    if isinstance(exc, (httpx.InvalidURL, httpx.HTTPError)):
        return "network"

    text = str(detail or "").lower()
    code = status_code

    if any(marker in text for marker in _BALANCE_MARKERS) or code == 402:
        return "balance"
    if any(marker in text for marker in _AUTH_MARKERS) or code in (401, 403):
        return "auth"
    if any(marker in text for marker in _RATE_MARKERS) or code == 429:
        return "rate_limit"
    if code == 404:
        return "not_found"
    if code is not None and 300 <= code < 400:
        return "redirect"
    if code is not None and code >= 500:
        return "server"
    if code is not None and code >= 400:
        return "http_error"
    return "unknown"


def _failure(
    *,
    latency_ms: int,
    checked_url: str,
    status_code: int | None,
    detail: str,
    error_code: str,
    model_id_found: bool | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "status_code": status_code,
        "checked_url": checked_url,
        "model_id_found": model_id_found,
        "detail": detail,
        "error_code": error_code,
    }


async def _resolve_addresses(hostname: str, port: int) -> list[str]:
    """DNS 解析；单测可 monkeypatch 本函数，避免依赖真实 DNS。"""
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise ProbeTargetBlocked("probe host cannot be resolved") from exc
    return sorted({str(row[4][0]).split("%", 1)[0] for row in rows})


async def _validate_probe_target(url: str) -> tuple[str, str]:
    """解析并准入目标主机；逻辑对齐 a2a_discovery._validate_target（允许私网 RFC1918）。

    允许：公网全局地址，或纯 RFC1918 私网（企业自建模型）。
    拒绝：回环、link-local（含云元数据 169.254.169.254）、组播等其它非全局地址。
    """
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ProbeTargetBlocked("invalid probe URL") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProbeTargetBlocked("invalid probe URL")
    addresses = await _resolve_addresses(parts.hostname, port)
    if not addresses:
        raise ProbeTargetBlocked("probe host cannot be resolved")
    resolved = [ipaddress.ip_address(address) for address in addresses]
    public_only = all(address.is_global for address in resolved)
    private_only = all(
        any(address in network for network in _PRIVATE_NETWORKS) for address in resolved
    )
    if not (public_only or private_only):
        raise ProbeTargetBlocked("target address is not allowed by network access policy")
    return str(parts.hostname).lower().rstrip("."), addresses[0]


def _pinned_request(
    client: httpx.AsyncClient,
    url: str,
    host: str,
    address: str,
    headers: dict[str, str],
) -> httpx.Request:
    """把请求打到已校验的 IP，Host/SNI 仍用原主机名，防止 DNS 重绑定。"""
    parts = urlsplit(url)
    ip_host = f"[{address}]" if ":" in address else address
    netloc = ip_host if parts.port is None else f"{ip_host}:{parts.port}"
    pinned_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    merged = dict(headers)
    merged["Host"] = parts.netloc
    request = client.build_request("GET", pinned_url, headers=merged)
    request.extensions["sni_hostname"] = host
    return request


async def _read_limited_body(resp: httpx.Response, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ProbeTargetBlocked("probe response exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def probe_model_template_connection(
    *,
    api_base: str,
    api_key: str,
    model_id: str,
    model_provider: str,
    timeout: float | int | None = None,
    verify_ssl: bool = False,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向 ``{api_base}/models`` 发 GET，校验连通与鉴权。

    返回结构化结果（不抛业务失败）；网络/协议异常也收成 ``ok=False``。
    失败时带 ``error_code``，前端按页面语言映射可读提示。
    """
    checked_url = models_list_url(api_base)
    raw_timeout = float(timeout) if timeout is not None else _DEFAULT_TIMEOUT_SEC
    if raw_timeout <= 0:
        raw_timeout = _DEFAULT_TIMEOUT_SEC
    timeout_sec = min(raw_timeout, _MAX_TIMEOUT_SEC)
    headers = build_probe_headers(
        api_key=api_key, model_provider=model_provider, data=data
    )

    started = time.perf_counter()
    try:
        host, pinned_address = await _validate_probe_target(checked_url)
        async with httpx.AsyncClient(
            timeout=timeout_sec,
            verify=bool(verify_ssl),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            request = _pinned_request(
                client, checked_url, host, pinned_address, headers
            )
            resp = await client.send(request, stream=True)
            try:
                raw = await _read_limited_body(resp, max_bytes=MAX_PROBE_RESPONSE_BYTES)
            finally:
                await resp.aclose()
    except ProbeTargetBlocked as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = str(exc) or "target blocked"
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=None,
            detail=detail,
            error_code=classify_connection_error(detail=detail, exc=exc),
        )
    except httpx.TimeoutException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = f"request timed out: {exc}"
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=None,
            detail=detail,
            error_code=classify_connection_error(detail=detail, exc=exc),
        )
    except httpx.InvalidURL as exc:
        # 不继承 HTTPError；非法端口等可通过 pydantic scheme/netloc 校验后在此抛出。
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = f"invalid url: {exc}"
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=None,
            detail=detail,
            error_code=classify_connection_error(detail=detail, exc=exc),
        )
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = f"request failed: {exc}"
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=None,
            detail=detail,
            error_code=classify_connection_error(detail=detail, exc=exc),
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    # 不跟随重定向；非 2xx（含 3xx 门户/强制 https）一律视为探测失败，避免假成功。
    if resp.status_code < 200 or resp.status_code >= 300:
        detail = _error_detail_from_bytes(resp.status_code, raw)
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=resp.status_code,
            detail=detail,
            error_code=classify_connection_error(
                status_code=resp.status_code, detail=detail
            ),
        )

    model_id_found: bool | None = None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        model_id_found = _model_id_in_payload(payload, model_id)
    except ValueError:
        payload = None

    # 服务商目录可解析且明确不含该 model_id 时判定失败（与仅探测 API 可达区分开）。
    if model_id_found is False:
        return _failure(
            latency_ms=latency_ms,
            checked_url=checked_url,
            status_code=resp.status_code,
            detail=f"model_id {model_id!r} was not listed by provider",
            error_code="model_not_found",
            model_id_found=False,
        )

    if model_id_found is True:
        detail = "reachable; model_id found in provider catalog"
    else:
        # 目录无法解析时无法核对 model_id，仅确认 API Base / Key 可达。
        detail = "reachable"

    return {
        "ok": True,
        "latency_ms": latency_ms,
        "status_code": resp.status_code,
        "checked_url": checked_url,
        "model_id_found": model_id_found,
        "detail": detail,
        "error_code": None,
    }
