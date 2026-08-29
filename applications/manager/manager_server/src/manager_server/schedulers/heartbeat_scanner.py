"""定时巡检：Manager 主动探活 Gateway / Runtime 健康检查接口，更新 online/offline。"""

from __future__ import annotations

import asyncio

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance.config_host_probe import check_config_host_alive
from manager_server.core.instance.instance_service import apply_health_probe_result
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF

_log = get_logger(__name__)
_TABLE = INSTANCE_INFO_TABLE_DEF.table_name
_PROBE_TIMEOUT = 5.0
_PROBE_CONCURRENCY = 20


async def _probe_one_side(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    side: str,
    host: str | None,
) -> tuple[str, str, bool | None]:
    """返回 ``(jid, side, result)``；``result`` 为 None 表示跳过（无 host）。"""
    base = str(host or "").strip().rstrip("/")
    if not base:
        return jiuwenclaw_id, side, None
    alive = await check_config_host_alive(
        base, side=side, timeout=_PROBE_TIMEOUT  # type: ignore[arg-type]
    )
    await apply_health_probe_result(
        handler,
        jiuwenclaw_id=jiuwenclaw_id,
        service_type=side,
        alive=alive,
    )
    return jiuwenclaw_id, side, alive


async def scan_instance_health_once(handler: DBHandler) -> dict[str, int]:
    """对所有带 config_host 的实例做一轮探活。"""
    rows = await handler.list_records(_TABLE, {}, limit=10_000, offset=0)
    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def _guarded(coro):
        async with sem:
            return await coro

    tasks = []
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
        if not jid:
            continue
        gw_host = getattr(row, "gateway_config_host", None)
        rt_host = getattr(row, "runtime_config_host", None)
        if gw_host:
            tasks.append(
                _guarded(
                    _probe_one_side(
                        handler, jiuwenclaw_id=jid, side="gateway", host=gw_host
                    )
                )
            )
        # runtime 探活与创建时策略一致：有 host 才探（probe 实现已支持 /healthz）
        if rt_host:
            tasks.append(
                _guarded(
                    _probe_one_side(
                        handler, jiuwenclaw_id=jid, side="runtime", host=rt_host
                    )
                )
            )

    stats = {"probed": 0, "alive": 0, "dead": 0, "skipped": 0}
    if not tasks:
        return stats

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            _log.warning("health_probe_task_failed", error=str(item))
            continue
        _jid, _side, alive = item
        if alive is None:
            stats["skipped"] += 1
            continue
        stats["probed"] += 1
        if alive:
            stats["alive"] += 1
        else:
            stats["dead"] += 1
    return stats


async def run_heartbeat_scan_loop(stop: asyncio.Event, handler: DBHandler) -> None:
    """周期探活循环（沿用原 heartbeat scanner 入口名，便于 app 启动挂载）。"""
    interval = max(15, int(settings.MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS or 60))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            stats = await scan_instance_health_once(handler)
            if stats["probed"]:
                _log.info(
                    "health_probe_scan",
                    probed=stats["probed"],
                    alive=stats["alive"],
                    dead=stats["dead"],
                )
        except Exception as exc:  # noqa: BLE001
            _log.warning("health_probe_scan_failed", error=str(exc))
