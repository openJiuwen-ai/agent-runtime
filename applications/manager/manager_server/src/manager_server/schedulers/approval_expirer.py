"""定时收敛：把已过期的 pending 审批单置为 expired。"""

from __future__ import annotations

import asyncio
import socket

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.approval.service import ApprovalService
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.logger import get_logger

_log = get_logger(__name__)

# 过期收敛间隔：两次扫描之间仍由 act() 的条件更新兜住正确性。
SCAN_INTERVAL_SECONDS = 300
_SCAN_LOCK_KEY = "manager:approval:expire-lock"
_SCAN_LOCK_TTL_MS = 240_000


async def run_approval_expirer_loop(stop: asyncio.Event, handler: DBHandler, redis) -> None:
    """周期扫描；redis 可用时每轮抢锁，只让一个副本执行。"""
    owner = socket.gethostname()
    service = ApprovalService(handler)
    while not stop.is_set():
        try:
            if redis is not None and not await redis.set(
                f"{settings.redis_key_prefix}{_SCAN_LOCK_KEY}",
                owner,
                nx=True,
                px=_SCAN_LOCK_TTL_MS,
            ):
                _log.info("approval expire lock held by another replica, skip this round")
            else:
                expired = await service.expire_overdue_orders()
                if expired:
                    _log.info("approval_orders_expired", count=expired)
        except Exception as exc:  # noqa: BLE001
            _log.warning("approval_expirer_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=SCAN_INTERVAL_SECONDS)
        except TimeoutError:
            continue
