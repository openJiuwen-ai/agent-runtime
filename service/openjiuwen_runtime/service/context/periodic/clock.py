# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""用 Redis TIME 对表，之后用本机 monotonic 推算「现在」。

避免每个「现在几点」都打一次 TIME（RTT 会吃掉 80ms 集合窗口）。
"""

from __future__ import annotations

import time
from typing import Any


def _as_int(v: Any) -> int:
    if isinstance(v, bytes):
        return int(v)
    return int(v)


async def redis_unix_now(redis: Any) -> float:
    """Redis TIME → unix 秒（含微秒小数）。"""
    pair = await redis.time()
    sec, usec = pair[0], pair[1]
    return float(_as_int(sec)) + float(_as_int(usec)) / 1_000_000.0


class RedisAlignedClock:
    """offset = Redis unix − monotonic；now() = monotonic + offset。"""

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._offset = 0.0

    async def sync(self) -> float:
        """打一次 TIME，刷新偏移，返回对表后的现在。"""
        rnow = await redis_unix_now(self._redis)
        self._offset = rnow - time.monotonic()
        return self.now()

    def now(self) -> float:
        return time.monotonic() + self._offset
