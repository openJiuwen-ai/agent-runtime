"""Server-side discovery of outbound A2A Agent Cards."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.parse import unquote
from uuid import uuid4

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.template.a2a_discovery_settings import A2ADiscoverySettingsService
from manager_server.infrastructure.utils import utc_now
from manager_server.models.template_models import A2A_OUTBOUND_DISCOVERY_TABLE_DEF

DEFAULT_CARD_PATH = "/.well-known/agent-card.json"
DISCOVERY_TTL_SECONDS = 600
MAX_CARD_BYTES = 1_048_576
MAX_DISCOVERY_CANDIDATES = 100
_DISCOVERY_TABLE = A2A_OUTBOUND_DISCOVERY_TABLE_DEF.table_name
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class A2ADiscoveryError(ValueError):
    def __init__(self, code: str, summary: str) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass(frozen=True)
class DiscoveredCard:
    source_url: str
    card_path: str
    card_url: str
    card_fingerprint: str
    agent_card: dict[str, Any]
    selected_interface: dict[str, str]


def _normalize_url(url: str, card_path: str | None) -> tuple[str, str, str]:
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise A2ADiscoveryError("DISCOVERY_URL_INVALID", "invalid discovery URL") from exc
    if (
        not text
        or len(text) > 2048
        or parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise A2ADiscoveryError("DISCOVERY_URL_INVALID", "invalid discovery URL")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower().rstrip(".")
    default_port = 443 if scheme == "https" else 80
    netloc = f"[{host}]" if ":" in host else host
    if port and port != default_port:
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    direct_card = card_path is None and path != "/"
    normalized_path = path if direct_card else str(card_path or DEFAULT_CARD_PATH).strip()
    decoded_path = unquote(normalized_path)
    if (
        not normalized_path.startswith("/")
        or normalized_path.startswith("//")
        or "\\" in normalized_path
        or "?" in normalized_path
        or "#" in normalized_path
        or decoded_path.startswith("//")
        or "\\" in decoded_path
        or ".." in decoded_path.split("/")
    ):
        raise A2ADiscoveryError("DISCOVERY_URL_INVALID", "invalid Agent Card path")
    source_path = "/" if direct_card else path.rstrip("/") or "/"
    source_url = urlunsplit((scheme, netloc, source_path, "", ""))
    card_url = (
        urlunsplit((scheme, netloc, normalized_path, "", ""))
        if direct_card
        else urljoin(f"{source_url.rstrip('/')}/", normalized_path.lstrip("/"))
    )
    return source_url, normalized_path, card_url


async def _validate_target(
    url: str,
    *,
    allow_http: bool = False,
    allow_loopback: bool = False,
    allow_private_network: bool = False,
    allow_public_http: bool = False,
) -> tuple[str, str]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if parts.scheme == "http" and not allow_http:
        raise A2ADiscoveryError("DISCOVERY_BLOCKED", "discovery requires HTTPS")
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise A2ADiscoveryError("CARD_FETCH_FAILED", "Agent Card host cannot be resolved") from exc
    addresses = sorted({str(row[4][0]).split("%", 1)[0] for row in rows})
    resolved = [ipaddress.ip_address(address) for address in addresses]
    loopback_only = bool(resolved) and all(address.is_loopback for address in resolved)
    public_only = bool(resolved) and all(address.is_global for address in resolved)
    private_only = bool(resolved) and all(
        any(address in network for network in _PRIVATE_NETWORKS) for address in resolved
    )
    if not (
        public_only
        or (allow_loopback and loopback_only)
        or (allow_private_network and private_only)
    ):
        raise A2ADiscoveryError("DISCOVERY_BLOCKED", "private network targets are not allowed")
    if parts.scheme == "http" and public_only and not allow_public_http:
        raise A2ADiscoveryError("DISCOVERY_BLOCKED", "discovery requires HTTPS")
    return str(parts.hostname).lower().rstrip("."), addresses[0]


def _pinned_request(client: httpx.AsyncClient, url: str, host: str, address: str) -> httpx.Request:
    parts = urlsplit(url)
    ip_host = f"[{address}]" if ":" in address else address
    netloc = ip_host if parts.port is None else f"{ip_host}:{parts.port}"
    pinned_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    host_header = parts.netloc
    request = client.build_request(
        "GET", pinned_url, headers={"Accept": "application/json", "Host": host_header}
    )
    request.extensions["sni_hostname"] = host
    return request


def _select_interface(card: dict[str, Any]) -> dict[str, str]:
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list):
        interfaces = card.get("additionalInterfaces")
    candidates = interfaces if isinstance(interfaces, list) else []
    if isinstance(card.get("url"), str):
        candidates = [
            {
                "url": card["url"],
                "protocolBinding": card.get("preferredTransport", "JSONRPC"),
                "protocolVersion": card.get("protocolVersion", ""),
            },
            *candidates,
        ]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        binding = str(item.get("protocolBinding") or item.get("transport") or "")
        url = str(item.get("url") or "").strip()
        protocol_version = str(
            item.get("protocolVersion") or card.get("protocolVersion") or ""
        ).strip()
        if binding.upper() == "JSONRPC" and url and protocol_version:
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return {
                    "url": url,
                    "protocol_binding": "JSONRPC",
                    "protocol_version": protocol_version,
                }
    raise A2ADiscoveryError("CARD_INVALID", "Agent Card has no compatible JSON-RPC interface")


async def fetch_agent_card(
    url: str,
    card_path: str | None = None,
    *,
    allow_http: bool = False,
    allow_loopback: bool = False,
    allow_private_network: bool = False,
    allow_public_http: bool = False,
) -> DiscoveredCard:
    source_url, normalized_path, card_url = _normalize_url(url, card_path)
    host, pinned_address = await _validate_target(
        card_url,
        allow_http=allow_http,
        allow_loopback=allow_loopback,
        allow_private_network=allow_private_network,
        allow_public_http=allow_public_http,
    )
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10, trust_env=False) as client:
            response = await client.send(_pinned_request(client, card_url, host, pinned_address))
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise A2ADiscoveryError("CARD_FETCH_FAILED", "Agent Card request failed") from exc
    if len(response.content) > MAX_CARD_BYTES:
        raise A2ADiscoveryError("CARD_INVALID", "Agent Card is too large")
    try:
        card = response.json()
    except ValueError as exc:
        raise A2ADiscoveryError("CARD_INVALID", "Agent Card is not valid JSON") from exc
    if not isinstance(card, dict) or not str(card.get("name") or "").strip():
        raise A2ADiscoveryError("CARD_INVALID", "Agent Card must contain a name")
    selected = _select_interface(card)
    await _validate_target(
        selected["url"],
        allow_http=allow_http,
        allow_loopback=allow_loopback,
        allow_private_network=allow_private_network,
        allow_public_http=allow_public_http,
    )
    canonical = json.loads(json.dumps(card, sort_keys=True, separators=(",", ":")))
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DiscoveredCard(
        source_url=source_url,
        card_path=normalized_path,
        card_url=card_url,
        card_fingerprint=f"sha256:{digest}",
        agent_card=canonical,
        selected_interface=selected,
    )


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _card_payload(card: DiscoveredCard) -> dict[str, Any]:
    return {
        "source_url": card.source_url,
        "card_path": card.card_path,
        "card_url": card.card_url,
        "card_fingerprint": card.card_fingerprint,
        "agent_card": card.agent_card,
        "selected_interface": card.selected_interface,
    }


def _card_from_payload(payload: dict[str, Any]) -> DiscoveredCard:
    return DiscoveredCard(
        source_url=str(payload["source_url"]),
        card_path=str(payload["card_path"]),
        card_url=str(payload["card_url"]),
        card_fingerprint=str(payload["card_fingerprint"]),
        agent_card=dict(payload["agent_card"]),
        selected_interface=dict(payload["selected_interface"]),
    )


async def create_candidate(
    handler: DBHandler, url: str, card_path: str | None = None
) -> dict[str, Any]:
    settings = await A2ADiscoverySettingsService(handler).get()
    card = await fetch_agent_card(
        url,
        card_path,
        allow_http=settings.allow_http,
        allow_loopback=settings.allow_loopback,
        allow_private_network=settings.allow_private_network,
        allow_public_http=settings.allow_public_http,
    )
    now = utc_now()
    rows = await handler.list_records(
        _DISCOVERY_TABLE,
        {},
        limit=MAX_DISCOVERY_CANDIDATES + 1,
        offset=0,
        order_by=[("created_at", False)],
    )
    for row in rows:
        if _as_utc(row.expires_at) <= _as_utc(now):
            await handler.delete(_DISCOVERY_TABLE, {"discovery_id": row.discovery_id})
    remaining = await handler.count_records(_DISCOVERY_TABLE, {})
    if remaining >= MAX_DISCOVERY_CANDIDATES:
        raise A2ADiscoveryError("DISCOVERY_LIMIT_REACHED", "too many pending discoveries")
    discovery_id = f"disc_{uuid4().hex}"
    expires_at = now + timedelta(seconds=DISCOVERY_TTL_SECONDS)
    await handler.create(
        _DISCOVERY_TABLE,
        {
            "discovery_id": discovery_id,
            "payload": _card_payload(card),
            "expires_at": expires_at,
            "created_at": now,
        },
    )
    return {
        "discovery_id": discovery_id,
        "expires_at": _as_utc(expires_at).isoformat().replace("+00:00", "Z"),
        "source_url": card.source_url,
        "card_path": card.card_path,
        "card_url": card.card_url,
        "card_fingerprint": card.card_fingerprint,
        "agent_card": card.agent_card,
        "selected_interface": card.selected_interface,
    }


async def get_candidate(handler: DBHandler, discovery_id: str) -> DiscoveredCard:
    row = await handler.get(_DISCOVERY_TABLE, {"discovery_id": discovery_id})
    if row is None or _as_utc(row.expires_at) <= _as_utc(utc_now()):
        if row is not None:
            await handler.delete(_DISCOVERY_TABLE, {"discovery_id": discovery_id})
        raise A2ADiscoveryError("DISCOVERY_NOT_FOUND", "discovery candidate not found or expired")
    payload = row.payload
    if isinstance(payload, str):
        payload = json.loads(payload)
    return _card_from_payload(payload)


async def delete_candidate(handler: DBHandler, discovery_id: str) -> None:
    await handler.delete(_DISCOVERY_TABLE, {"discovery_id": discovery_id})


def critical_identity(card: dict[str, Any], selected: dict[str, Any]) -> tuple[Any, ...]:
    schemes = card.get("securitySchemes")
    scheme_names = sorted(schemes) if isinstance(schemes, dict) else []
    provider = card.get("provider")
    return (
        selected.get("url"),
        str(selected.get("protocol_binding") or "").upper(),
        card.get("securityRequirements") or [],
        scheme_names,
        provider.get("url") if isinstance(provider, dict) else None,
        [item.get("protected") for item in card.get("signatures") or [] if isinstance(item, dict)],
    )
