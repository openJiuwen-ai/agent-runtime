# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""NTP 同步时钟（FR1.1.1.2）。

时间源地址禁止硬编码，一律由配置注入。单测通过 NtpClient 协议注入假源。
全部源不可达时降级本地时钟，不阻断调用方。
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .constants import (
    EVENT_NTP_OFFSET_EXCEEDED,
    EVENT_NTP_SOURCE_FAILOVER,
    NTP_FAILOVER_DEFAULT,
    NTP_MAX_OFFSET_MS_DEFAULT,
    NTP_SYNC_INTERVAL_DEFAULT,
)
from .errors import ClockSyncError
from .models import ClockEvent

# NTP 时间从 1900-01-01 起算，Unix 从 1970-01-01。
_NTP_UNIX_DELTA = 2208988800
_NTP_PACKET = b"\x1b" + 47 * b"\x00"


def parse_sync_interval(value: str | int | float) -> float:
    """解析 ntp.sync_interval（如 300s / 5m / 1h）为秒。"""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            raise ValueError("ntp.sync_interval must be positive")
        return seconds
    raw = str(value).strip().lower()
    units = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    for suffix, mul in units.items():
        if raw.endswith(suffix) and raw[: -len(suffix)].replace(".", "", 1).isdigit():
            seconds = float(raw[: -len(suffix)]) * mul
            if seconds <= 0:
                raise ValueError("ntp.sync_interval must be positive")
            return seconds
    if raw.replace(".", "", 1).isdigit():
        seconds = float(raw)
        if seconds <= 0:
            raise ValueError("ntp.sync_interval must be positive")
        return seconds
    raise ValueError(f"invalid ntp.sync_interval: {value!r}")


@dataclass(frozen=True)
class NtpConfig:
    """NTP 时间源配置。servers 默认为空，必须由部署注入。"""

    servers: tuple[str, ...] = ()
    sync_interval: str = NTP_SYNC_INTERVAL_DEFAULT
    max_offset_ms: int = NTP_MAX_OFFSET_MS_DEFAULT
    failover: bool = NTP_FAILOVER_DEFAULT
    query_timeout_seconds: float = 2.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> NtpConfig:
        if not data:
            return cls()
        servers = data.get("servers") or ()
        return cls(
            servers=tuple(str(s) for s in servers if str(s).strip()),
            sync_interval=str(data.get("sync_interval", NTP_SYNC_INTERVAL_DEFAULT)),
            max_offset_ms=int(data.get("max_offset_ms", NTP_MAX_OFFSET_MS_DEFAULT)),
            failover=bool(data.get("failover", NTP_FAILOVER_DEFAULT)),
            query_timeout_seconds=float(data.get("query_timeout_seconds", 2.0)),
        )

    @property
    def sync_interval_seconds(self) -> float:
        return parse_sync_interval(self.sync_interval)


class NtpClient(Protocol):
    """可注入的 NTP 查询客户端。"""

    def query(self, server: str, timeout: float) -> float:
        """返回该时间源的 Unix 时间戳（秒，浮点）。失败则抛异常。"""
        ...


class UdpNtpClient:
    """标准 NTP（UDP/123）客户端，无第三方依赖。"""

    def query(self, server: str, timeout: float) -> float:
        host = server.strip()
        if not host or host.startswith("<") or " " in host:
            raise ClockSyncError(f"invalid ntp server: {server!r}")
        port = 123
        if ":" in host and not host.count(":") > 1:
            # host:port（IPv4）
            maybe_host, maybe_port = host.rsplit(":", 1)
            if maybe_port.isdigit():
                host, port = maybe_host, int(maybe_port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(_NTP_PACKET, (host, port))
            data, _ = sock.recvfrom(1024)
        if len(data) < 48:
            raise ClockSyncError("ntp response too short")
        unpacked = struct.unpack("!12I", data[:48])
        seconds = unpacked[10] - _NTP_UNIX_DELTA
        frac = unpacked[11] / 2**32
        return seconds + frac


class LocalClock:
    """未同步的本地时钟，仅作降级与单测基线。"""

    def now(self) -> float:
        return time.time()


@dataclass
class SyncResult:
    """一次 sync_once 的结果。"""

    ok: bool
    source: str | None
    offset_seconds: float
    degraded: bool
    events: list[ClockEvent] = field(default_factory=list)


class SyncedClock:
    """启动同步 + 周期同步；now() = 本地时钟 + 最近一次 offset。"""

    def __init__(
        self,
        config: NtpConfig | None = None,
        *,
        client: NtpClient | None = None,
        time_fn=time.time,
    ) -> None:
        self.config = config or NtpConfig()
        self._client: NtpClient = client or UdpNtpClient()
        self._time_fn = time_fn
        self._offset = 0.0
        self._degraded = True if not self.config.servers else False
        self._source: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_events: list[ClockEvent] = []

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def source(self) -> str | None:
        return self._source

    def now(self) -> float:
        return self._time_fn() + self._offset

    def drain_events(self) -> list[ClockEvent]:
        with self._lock:
            events = list(self._pending_events)
            self._pending_events.clear()
            return events

    def sync_once(self) -> SyncResult:
        events: list[ClockEvent] = []
        servers = self.config.servers
        if not servers:
            self._degraded = True
            self._source = None
            self._offset = 0.0
            return SyncResult(
                ok=False,
                source=None,
                offset_seconds=0.0,
                degraded=True,
                events=events,
            )

        last_error: Exception | None = None
        for index, server in enumerate(servers):
            try:
                remote = self._client.query(server, self.config.query_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 — 查询失败即试下一个源
                last_error = exc
                if not self.config.failover:
                    break
                continue

            local = self._time_fn()
            offset = remote - local
            offset_ms = abs(offset) * 1000.0
            if index > 0:
                events.append(
                    ClockEvent(
                        event=EVENT_NTP_SOURCE_FAILOVER,
                        level="WARN",
                        message=f"主NTP源不可达，已切换至备用源 {server}",
                        rspcd="0000",
                        details={"source": server, "index": index},
                    )
                )
            if offset_ms > self.config.max_offset_ms:
                events.append(
                    ClockEvent(
                        event=EVENT_NTP_OFFSET_EXCEEDED,
                        level="WARN",
                        message=(
                            f"本地时钟偏差{int(round(offset_ms))}ms"
                            f"超过阈值{self.config.max_offset_ms}ms"
                        ),
                        rspcd="E005",
                        details={
                            "offset_ms": offset_ms,
                            "threshold_ms": self.config.max_offset_ms,
                            "source": server,
                        },
                    )
                )
            with self._lock:
                self._offset = offset
                self._degraded = False
                self._source = server
                self._pending_events.extend(events)
            return SyncResult(
                ok=True,
                source=server,
                offset_seconds=offset,
                degraded=False,
                events=events,
            )

        # 全部不可达：降级本地时钟，持续告警语义交给调用方写 #EVT。
        events.append(
            ClockEvent(
                event=EVENT_NTP_SOURCE_FAILOVER,
                level="WARN",
                message="全部NTP源不可达，已降级为本地时钟",
                rspcd="E005",
                details={"error": str(last_error) if last_error else "no servers reachable"},
            )
        )
        with self._lock:
            self._offset = 0.0
            self._degraded = True
            self._source = None
            self._pending_events.extend(events)
        return SyncResult(
            ok=False,
            source=None,
            offset_seconds=0.0,
            degraded=True,
            events=events,
        )

    def start_periodic(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            interval = self.config.sync_interval_seconds
            while not self._stop.wait(interval):
                self.sync_once()

        self._thread = threading.Thread(
            target=_loop, name="audit-ntp-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None


class SequenceNtpClient:
    """测试用：按服务器名返回时间戳或抛出异常。"""

    def __init__(self, responses: Mapping[str, float | BaseException]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def query(self, server: str, timeout: float) -> float:
        self.calls.append(server)
        value = self.responses.get(server)
        if value is None:
            raise ClockSyncError(f"unknown ntp server {server!r}")
        if isinstance(value, BaseException):
            raise value
        return float(value)
