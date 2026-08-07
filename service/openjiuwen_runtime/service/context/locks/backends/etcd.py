# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""etcd v3 lease/CAS lock backend implemented with ``aetcd``."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from ....errors import InvalidLockLease, LockBackendUnavailable, LockLost
from ..base import LockCapabilities, LockCredential


@dataclass(frozen=True, slots=True)
class EtcdEndpoint:
    """Parsed endpoint used by the aetcd client factory."""

    scheme: str
    host: str
    port: int

    @property
    def tls(self) -> bool:
        return self.scheme in {"https", "grpcs"}


@dataclass(slots=True)
class _OwnedLease:
    lease: Any
    key: str
    token: str


def parse_etcd_endpoint(endpoint: str) -> EtcdEndpoint:
    value = endpoint.strip()
    if not value:
        raise ValueError("etcd endpoint must not be empty")
    parsed = urlparse(value if "://" in value else f"http://{value}")
    if parsed.scheme not in {"http", "https", "grpc", "grpcs"} or not parsed.hostname:
        raise ValueError(f"invalid etcd endpoint: {endpoint!r}")
    default_port = 2379
    port = parsed.port or default_port
    if not 1 <= port <= 65535:
        raise ValueError("etcd endpoint port must be between 1 and 65535")
    return EtcdEndpoint(parsed.scheme, parsed.hostname, port)


def create_etcd_client(
    endpoints: str | list[str] | tuple[str, ...],
    *,
    username: str | None = None,
    password: str | None = None,
    connect_timeout: float | None = None,
    tls_ca_cert: str | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> Any:
    """Create an ``aetcd`` client from endpoint, TLS and authentication settings."""
    import aetcd

    values = endpoints.split(",") if isinstance(endpoints, str) else list(endpoints)
    parsed = [parse_etcd_endpoint(value) for value in values]
    if not parsed:
        raise ValueError("at least one etcd endpoint is required")
    if (username is None) != (password is None):
        raise ValueError("etcd username and password must be configured together")
    if (tls_cert is None) != (tls_key is None):
        raise ValueError("etcd client certificate and key must be configured together")
    timeout = (
        None if connect_timeout is None else max(1, math.ceil(float(connect_timeout)))
    )
    clients = [
        _build_aetcd_client(
            aetcd,
            endpoint,
            username=username,
            password=password,
            timeout=timeout,
            tls_ca_cert=tls_ca_cert,
            tls_cert=tls_cert,
            tls_key=tls_key,
        )
        for endpoint in parsed
    ]
    return clients[0] if len(clients) == 1 else _EtcdClientPool(clients)


def _build_aetcd_client(
    aetcd: Any,
    endpoint: EtcdEndpoint,
    *,
    username: str | None,
    password: str | None,
    timeout: int | None,
    tls_ca_cert: str | None,
    tls_cert: str | None,
    tls_key: str | None,
) -> Any:
    kwargs = {
        "host": endpoint.host,
        "port": endpoint.port,
        "username": username,
        "password": password,
        "timeout": timeout,
    }
    if not endpoint.tls and not any((tls_ca_cert, tls_cert, tls_key)):
        return aetcd.Client(**kwargs)

    import grpc

    root_certificates = Path(tls_ca_cert).read_bytes() if tls_ca_cert else None
    certificate_chain = Path(tls_cert).read_bytes() if tls_cert else None
    private_key = Path(tls_key).read_bytes() if tls_key else None
    credentials = grpc.ssl_channel_credentials(
        root_certificates=root_certificates,
        private_key=private_key,
        certificate_chain=certificate_chain,
    )

    class _SecureAetcdClient(aetcd.Client):
        async def connect(self) -> None:
            if self._connected.is_set():
                return
            if self._is_connecting:
                await asyncio.wait_for(
                    self._connected.wait(), self._connect_wait_timeout
                )
                return
            try:
                self._is_connecting = True
                target = f"{self._host}:{self._port}"
                self.channel = aetcd.rpc.secure_channel(
                    target,
                    credentials,
                    options=self._options.items(),
                )
                if self._username is not None and self._password is not None:
                    self.auth_stub = aetcd.rpc.AuthStub(self.channel)
                    request = aetcd.rpc.AuthenticateRequest(
                        name=self._username,
                        password=self._password,
                    )
                    response = await self.auth_stub.Authenticate(
                        request,
                        timeout=self._timeout,
                    )
                    self.metadata = (("token", response.token),)
                self.kvstub = aetcd.rpc.KVStub(self.channel)
                self.clusterstub = aetcd.rpc.ClusterStub(self.channel)
                self.leasestub = aetcd.rpc.LeaseStub(self.channel)
                self.maintenancestub = aetcd.rpc.MaintenanceStub(self.channel)
                self._watcher = aetcd.watcher.Watcher(
                    aetcd.rpc.WatchStub(self.channel),
                    timeout=self._timeout,
                    metadata=self.metadata,
                )
                self._connected.set()
            finally:
                self._is_connecting = False

    return _SecureAetcdClient(**kwargs)


class _PooledLease:
    def __init__(self, client: "_EtcdClientPool", lease_id: int, ttl: int) -> None:
        self._client = client
        self.id = lease_id
        self.ttl = ttl

    async def refresh(self) -> Any:
        return await self._client.refresh_lease(self.id)

    async def revoke(self) -> None:
        await self._client.revoke_lease(self.id)


class _EtcdClientPool:
    """Small endpoint failover facade over pinned single-endpoint aetcd clients."""

    def __init__(self, clients: list[Any]) -> None:
        self._clients = clients
        self._active = 0
        self.transactions = clients[0].transactions

    async def connect(self) -> None:
        await self._call("connect")

    async def status(self) -> Any:
        return await self._call("status")

    async def get(self, key: bytes) -> Any:
        return await self._call("get", key)

    async def transaction(self, compare: Any, success: Any, failure: Any) -> Any:
        return await self._call(
            "transaction",
            compare=compare,
            success=success,
            failure=failure,
        )

    async def lease(self, ttl: int) -> _PooledLease:
        lease = await self._call("lease", ttl)
        return _PooledLease(self, int(lease.id), int(lease.ttl))

    async def refresh_lease(self, lease_id: int) -> Any:
        return await self._call("refresh_lease", lease_id)

    async def revoke_lease(self, lease_id: int) -> None:
        await self._call("revoke_lease", lease_id)

    async def close(self) -> None:
        await asyncio.gather(
            *(client.close() for client in self._clients),
            return_exceptions=True,
        )

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for offset in range(len(self._clients)):
            index = (self._active + offset) % len(self._clients)
            try:
                result = await getattr(self._clients[index], method)(*args, **kwargs)
                self._active = index
                return result
            except Exception as exc:  # noqa: BLE001 - try the next configured member
                last_error = exc
        if last_error is not None:
            raise last_error
        raise LockBackendUnavailable("no etcd endpoints are configured")


class EtcdLockBackend:
    """Distributed lock using an etcd lease and transaction CAS."""

    capabilities = LockCapabilities(distributed=True, fencing=True)

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "lock",
        owns_client: bool = False,
        instance_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self._client = client
        self.prefix = prefix
        self._owns_client = owns_client
        self.instance_id = instance_id
        self.request_id = request_id
        self._leases: dict[int, _OwnedLease] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def format_key(self, key: str) -> str:
        return f"{self.prefix}:{key}" if self.prefix else key

    @staticmethod
    def _ttl(ttl: float) -> int:
        ttl = float(ttl)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite positive number")
        return max(1, math.ceil(ttl))

    def _token(self) -> str:
        return f"{uuid4().hex}:{self.instance_id or '-'}:{self.request_id or '-'}"

    async def try_acquire(self, key: str, ttl: float) -> LockCredential | None:
        self._ensure_open()
        full_key = self.format_key(key)
        lease = await self._new_lease(ttl)
        lease_id = int(lease.id)
        token = self._token()
        retain_lease = False
        try:
            try:
                succeeded, _ = await self._client.transaction(
                    compare=[self._client.transactions.create(full_key.encode()) == 0],
                    success=[
                        self._client.transactions.put(
                            full_key.encode(),
                            token.encode(),
                            lease=lease_id,
                        )
                    ],
                    failure=[],
                )
            except Exception as exc:  # noqa: BLE001 - normalize transport errors
                raise LockBackendUnavailable(
                    f"etcd lock acquisition failed: {exc}"
                ) from exc
            if not succeeded:
                return None
            try:
                current = await self._client.get(full_key.encode())
            except Exception as exc:  # noqa: BLE001 - normalize transport errors
                raise LockBackendUnavailable(
                    f"etcd lock acquisition failed: {exc}"
                ) from exc
            if current is None or current.value != token.encode():
                raise LockLost(f"lock {full_key!r} was lost during acquisition")
            acquired_at = time.monotonic()
            credential = LockCredential(
                key=full_key,
                token=token,
                backend="etcd",
                lease_id=lease_id,
                fencing_token=int(current.create_revision),
                acquired_at=acquired_at,
                expires_at=acquired_at + float(ttl),
            )
            async with self._lock:
                self._leases[lease_id] = _OwnedLease(lease, full_key, token)
            retain_lease = True
            return credential
        finally:
            if not retain_lease:
                await self._revoke(lease)

    async def renew(self, credential: LockCredential, ttl: float) -> LockCredential:
        self._validate_credential(credential)
        self._ttl(ttl)
        lease = await self._known_lease(credential)
        try:
            current = await self._client.get(credential.key.encode())
        except Exception as exc:  # noqa: BLE001 - normalize transport errors
            raise LockBackendUnavailable(f"etcd lock renewal failed: {exc}") from exc
        if current is None or current.value != credential.token.encode():
            raise LockLost(f"lock {credential.key!r} is no longer owned")
        try:
            response = await lease.refresh()
        except Exception as exc:  # noqa: BLE001 - normalize transport errors
            raise LockBackendUnavailable(f"etcd lock renewal failed: {exc}") from exc
        if (
            response is None
            or getattr(response, "TTL", getattr(response, "ttl", 0)) <= 0
        ):
            raise LockLost(f"lock {credential.key!r} lease keepalive failed")
        return credential.renewed(ttl)

    async def release(self, credential: LockCredential) -> bool:
        self._validate_credential(credential)
        lease = await self._known_lease(credential, required=False)
        if lease is None:
            return False
        try:
            succeeded, _ = await self._client.transaction(
                compare=[
                    self._client.transactions.value(credential.key.encode())
                    == credential.token.encode()
                ],
                success=[self._client.transactions.delete(credential.key.encode())],
                failure=[],
            )
            await self._revoke(lease)
            async with self._lock:
                self._leases.pop(int(credential.lease_id), None)
            return bool(succeeded)
        except Exception as exc:  # noqa: BLE001
            raise LockBackendUnavailable(f"etcd lock release failed: {exc}") from exc

    async def ping(self) -> bool:
        self._ensure_open()
        transactions = getattr(self._client, "transactions", None)
        if transactions is None or any(
            not callable(getattr(transactions, name, None))
            for name in ("create", "value", "put", "delete")
        ):
            raise LockBackendUnavailable(
                "etcd client does not provide transaction operations"
            )
        try:
            await self._client.status()
            return True
        except Exception as exc:  # noqa: BLE001
            raise LockBackendUnavailable(f"etcd health check failed: {exc}") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            leases = tuple(owned.lease for owned in self._leases.values())
            self._leases.clear()
        for lease in leases:
            try:
                await self._revoke(lease)
            except Exception:  # noqa: BLE001
                pass
        if self._owns_client and hasattr(self._client, "close"):
            await self._client.close()

    async def _new_lease(self, ttl: float) -> Any:
        try:
            return await self._client.lease(self._ttl(ttl))
        except Exception as exc:  # noqa: BLE001
            raise LockBackendUnavailable(f"etcd lease creation failed: {exc}") from exc

    async def _known_lease(
        self, credential: LockCredential, *, required: bool = True
    ) -> Any | None:
        if credential.lease_id is None:
            if required:
                raise InvalidLockLease("etcd credential has no lease id")
            return None
        async with self._lock:
            owned = self._leases.get(int(credential.lease_id))
        if owned is not None and (
            owned.key != credential.key or owned.token != credential.token
        ):
            raise InvalidLockLease("etcd credential does not match its owned lease")
        lease = None if owned is None else owned.lease
        if lease is None and required:
            raise InvalidLockLease("etcd credential is not owned by this backend")
        return lease

    async def _revoke(self, lease: Any) -> None:
        try:
            await lease.revoke()
        except AttributeError:
            await self._client.revoke_lease(int(lease.id))

    @staticmethod
    def _validate_credential(credential: LockCredential) -> None:
        if credential.backend != "etcd":
            raise InvalidLockLease(
                f"credential backend {credential.backend!r} cannot be used with etcd"
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise LockBackendUnavailable("etcd lock backend is closed")


__all__ = [
    "EtcdEndpoint",
    "EtcdLockBackend",
    "create_etcd_client",
    "parse_etcd_endpoint",
]
