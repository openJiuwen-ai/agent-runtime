# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# pylint: disable=protected-access

"""audit 压测用例（默认不跑；显式 ``-m perf``）。

目标：给本阶段 ``log_audit`` / ``build_audit_attributes`` 一条粗粒度基线，
发现明显回归；不是精密 benchmark，阈值放宽以兼容开发机差异。
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from openjiuwen_runtime.foundation.audit import (
    AuditLogConfig,
    AuditManager,
    AuditSnapshot,
    MemoryEmitter,
    bind_audit_context,
    build_audit_attributes,
    reset_audit_context,
    reset_audit_manager,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.perf

_N_SINGLE = 10_000
_N_PER_THREAD = 2_500
_N_THREADS = 4
# 放宽阈值：主要挡数量级回归，不做精密性能门禁
_MIN_OPS_PER_SEC = 1_000
_MAX_WALL_SEC_SINGLE = 30.0


def _fields(**extra):
    base = {
        "UA": "perf",
        "RSPCD": "0000",
        "SUBMDL": "gateway",
        "PROC": "authenticate",
        "COST": 1,
    }
    base.update(extra)
    return base


@pytest.fixture()
def mem_manager():
    mem = MemoryEmitter()
    mgr = AuditManager(emitter=mem)
    reset_audit_manager(mgr)
    yield mem, mgr
    reset_audit_manager(AuditManager(emitter=MemoryEmitter()))


def test_log_audit_throughput_memory_emitter(mem_manager):
    mem, mgr = mem_manager
    tokens = bind_audit_context(
        session_id="sess_perf",
        request_id="req_perf",
        user_id="perf_user",
        src_ip="10.0.0.1",
        dst_ip="10.0.0.2",
    )
    fields = _fields()
    try:
        t0 = time.perf_counter()
        for i in range(_N_SINGLE):
            mgr.log_audit("UA", level="INFO", COST=i, **{k: v for k, v in fields.items() if k != "COST"})
        elapsed = time.perf_counter() - t0
    finally:
        reset_audit_context(tokens)

    ops = _N_SINGLE / elapsed if elapsed > 0 else float("inf")
    logger.info(
        "[perf] log_audit x%s: %.3fs, %.0f ops/s, records=%s",
        _N_SINGLE,
        elapsed,
        ops,
        len(mem.records),
    )
    assert len(mem.records) == _N_SINGLE
    assert elapsed < _MAX_WALL_SEC_SINGLE
    assert ops >= _MIN_OPS_PER_SEC


def test_log_audit_concurrent_threads(mem_manager):
    mem, mgr = mem_manager
    errors: list[BaseException] = []
    barrier = threading.Barrier(_N_THREADS)

    def worker(tid: int) -> None:
        tokens = bind_audit_context(
            session_id=f"sess_{tid}",
            request_id=f"req_{tid}",
            user_id=f"user_{tid}",
        )
        try:
            barrier.wait(timeout=5)
            for i in range(_N_PER_THREAD):
                mgr.log_audit(
                    "UA",
                    level="INFO",
                    UA=f"t{tid}-{i}",
                    RSPCD="0000",
                    SUBMDL="gateway",
                    PROC="authenticate",
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            reset_audit_context(tokens)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"audit-perf-{i}")
        for i in range(_N_THREADS)
    ]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    elapsed = time.perf_counter() - t0

    expected = _N_THREADS * _N_PER_THREAD
    ops = expected / elapsed if elapsed > 0 else float("inf")
    logger.info(
        "[perf] concurrent %sx%s: %.3fs, %.0f ops/s, records=%s",
        _N_THREADS,
        _N_PER_THREAD,
        elapsed,
        ops,
        len(mem.records),
    )
    assert not errors, f"worker errors: {errors!r}"
    assert all(not t.is_alive() for t in threads)
    assert len(mem.records) == expected
    assert ops >= _MIN_OPS_PER_SEC / 2  # 并发下阈值再放宽一半


def test_build_audit_attributes_hot_path():
    snap = AuditSnapshot.from_config(
        AuditLogConfig.from_dict(
            {
                "data_center": "N",
                "system_code": "99900180001",
                "node": "10.0.0.1:8080",
                "otel": {"enabled": False, "endpoint": "http://localhost:4317"},
            }
        )
    )
    tokens = bind_audit_context(
        session_id="sess_attr",
        request_id="req_attr",
        user_id="alice",
        src_ip="10.0.0.1",
        dst_ip="10.0.0.2",
    )
    fields = _fields()
    n = _N_SINGLE
    try:
        t0 = time.perf_counter()
        for _ in range(n):
            build_audit_attributes(
                snap,
                event_type="UA",
                level="INFO",
                fields=fields,
            )
        elapsed = time.perf_counter() - t0
    finally:
        reset_audit_context(tokens)

    ops = n / elapsed if elapsed > 0 else float("inf")
    logger.info(
        "[perf] build_audit_attributes x%s: %.3fs, %.0f ops/s",
        n,
        elapsed,
        ops,
    )
    assert elapsed < _MAX_WALL_SEC_SINGLE
    assert ops >= _MIN_OPS_PER_SEC * 2
