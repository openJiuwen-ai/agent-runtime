# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""FR1.1.1.2 NTP 同步时钟。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.audit.clock import (
    NtpConfig,
    SequenceNtpClient,
    SyncedClock,
    parse_sync_interval,
)
from openjiuwen_runtime.foundation.audit.constants import (
    EVENT_NTP_OFFSET_EXCEEDED,
    EVENT_NTP_SOURCE_FAILOVER,
)
from openjiuwen_runtime.foundation.audit.formatter import format_clock_event, parse_audit_line
from openjiuwen_runtime.foundation.audit.models import RuntimeIdentity


def test_parse_sync_interval():
    assert parse_sync_interval("300s") == 300
    assert parse_sync_interval("5m") == 300
    assert parse_sync_interval(10) == 10.0


def test_sync_primary_success_sets_offset():
    local = 1_000_000.0
    remote = 1_000_000.4
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a", "ntp-b"), max_offset_ms=500),
        client=SequenceNtpClient({"ntp-a": remote}),
        time_fn=lambda: local,
    )
    result = clock.sync_once()
    assert result.ok is True
    assert result.source == "ntp-a"
    assert result.degraded is False
    assert abs(result.offset_seconds - 0.4) < 1e-9
    assert clock.now() == local + 0.4
    assert result.events == []


def test_failover_to_secondary_emits_event():
    local = 1_000_000.0
    client = SequenceNtpClient(
        {
            "ntp-a": ConnectionError("down"),
            "ntp-b": local,
        }
    )
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a", "ntp-b"), failover=True, max_offset_ms=500),
        client=client,
        time_fn=lambda: local,
    )
    result = clock.sync_once()
    assert result.ok is True
    assert result.source == "ntp-b"
    assert client.calls == ["ntp-a", "ntp-b"]
    assert any(e.event == EVENT_NTP_SOURCE_FAILOVER for e in result.events)


def test_offset_exceeded_emits_warn_event():
    local = 1_000_000.0
    remote = 1_000_000.8  # 800ms
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a",), max_offset_ms=500),
        client=SequenceNtpClient({"ntp-a": remote}),
        time_fn=lambda: local,
    )
    result = clock.sync_once()
    assert result.ok is True
    assert any(e.event == EVENT_NTP_OFFSET_EXCEEDED for e in result.events)
    evt = result.events[0]
    line = format_clock_event(
        evt,
        clock=clock,
        identity=RuntimeIdentity(data_center="N", system_code="sys", node="n1"),
        header={"caller": "audit.clock:1", "pid": "1", "tid": "t"},
    )
    parsed = parse_audit_line(line)
    assert parsed.keyword == "EVT"
    assert parsed.header["level"] == "WARN"
    assert parsed.elements["EVT"] == EVENT_NTP_OFFSET_EXCEEDED
    assert parsed.elements["RSPCD"] == "E005"
    assert parsed.elements["SUBMDL"] == "audit"
    assert parsed.elements["PROC"] == "ntp_sync"


def test_all_sources_fail_degrades_to_local_clock():
    local = 42.0
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a", "ntp-b"), failover=True),
        client=SequenceNtpClient(
            {
                "ntp-a": TimeoutError("a"),
                "ntp-b": TimeoutError("b"),
            }
        ),
        time_fn=lambda: local,
    )
    result = clock.sync_once()
    assert result.ok is False
    assert result.degraded is True
    assert clock.degraded is True
    assert clock.now() == local
    assert any(e.event == EVENT_NTP_SOURCE_FAILOVER for e in result.events)


def test_no_servers_configured_stays_degraded():
    clock = SyncedClock(NtpConfig(servers=()), time_fn=lambda: 1.0)
    result = clock.sync_once()
    assert result.ok is False
    assert result.degraded is True
    assert clock.now() == 1.0


def test_failover_disabled_does_not_try_backup():
    client = SequenceNtpClient(
        {
            "ntp-a": ConnectionError("down"),
            "ntp-b": 1.0,
        }
    )
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a", "ntp-b"), failover=False),
        client=client,
        time_fn=lambda: 0.0,
    )
    result = clock.sync_once()
    assert result.ok is False
    assert client.calls == ["ntp-a"]


def test_drain_events_clears_pending():
    local = 1.0
    clock = SyncedClock(
        NtpConfig(servers=("a", "b")),
        client=SequenceNtpClient({"a": ConnectionError("x"), "b": local}),
        time_fn=lambda: local,
    )
    clock.sync_once()
    first = clock.drain_events()
    second = clock.drain_events()
    assert first
    assert second == []


def test_periodic_start_stop():
    clock = SyncedClock(
        NtpConfig(servers=("ntp-a",), sync_interval="1s"),
        client=SequenceNtpClient({"ntp-a": 1.0}),
        time_fn=lambda: 1.0,
    )
    clock.start_periodic()
    clock.start_periodic()  # idempotent
    clock.stop()
    assert clock._thread is None
