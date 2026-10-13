# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Redis EVAL 禁用环境兼容原语（WATCH/MULTI/EXEC 降级路径）。

背景：部分客户环境的公用 Redis 以安全限制禁用脚本命令
（``rename-command EVAL ""`` 或 ACL 无 eval 权限）。本模块提供框架层
共享的降级原语，供锁后端 / 周期任务协调器等使用：

- :func:`eval_disabled_error`：识别服务端对 EVAL 的两类拒绝
  （rename-command 报 ``unknown command``、ACL 报 ``noperm`` /
  ``no permissions``）；
- 进程级 memo（按 client 对象）：首个 EVAL 拒绝命中后标记，后续调用
  直接走 WATCH 路径，避免每拍重复撞墙刷错误日志；
- :func:`watch_cas`：owner-token 校验式 CAS（锁 renew/release 的
  无 Lua 等价物，自 ``RedisLockBackend._watch_cas`` 泛化上提）；
- :func:`eval_or_watch`：eval 优先、命中禁用错误即降级并 memo 的
  统一包装（惰性降级：框架独立使用方零配置可用）；
- :func:`backoff_delay`：WatchError 乐观重试的 jitter 退避。

等价性前提：Redis 事务（WATCH/MULTI/EXEC）与 Lua 同为单连接串行化
执行，键集同槽（hash tag）时语义等价；redis-py 的 ClusterPipeline
``transaction=True`` 支持同槽事务（跨槽在客户端侧即拒绝）。
崩溃窗口差异（独立命令与 EXEC 之间进程死亡）由各调用方的周期
自愈兜底，不属于本模块职责。
"""

from __future__ import annotations

import random
import weakref
from typing import Any, Callable, Optional

from openjiuwen_runtime.foundation.log import get_logger
from redis.exceptions import WatchError

logger = get_logger(__name__)

# 服务端拒绝 EVAL 的消息特征（小写子串对，两段都命中才判禁用）：
# - rename-command EVAL "" → "unknown command 'eval', with args beginning with:"
# - ACL 撤权               → "noperm ... 'eval'" / "has no permissions to run the 'eval'"
_EVAL_DISABLED_MARKERS: tuple[tuple[str, str], ...] = (
    ("unknown command", "eval"),
    ("noperm", "eval"),
    ("no permissions", "eval"),
)

# 按 client 对象的进程级 memo（弱引用：client 关闭即自动出集）
_EVAL_DISABLED_CLIENTS: "weakref.WeakSet[Any]" = weakref.WeakSet()


def eval_disabled_error(exc: Exception) -> bool:
    """判断异常是否为「服务端禁用 EVAL」的拒绝（而非网络/语法等其他错误）。"""
    msg = str(exc).lower()
    return any(a in msg and b in msg for a, b in _EVAL_DISABLED_MARKERS)


def mark_eval_disabled(client: Any, disabled: bool = True) -> None:
    """标记/清除该 client 的 EVAL 不可用（启动期主动置位或首撞后惰性标记）。"""
    if disabled:
        _EVAL_DISABLED_CLIENTS.add(client)
    else:
        _EVAL_DISABLED_CLIENTS.discard(client)


def is_eval_disabled(client: Any) -> bool:
    """该 client 是否已标记 EVAL 禁用（memo 命中则无需再试 eval）。"""
    return client in _EVAL_DISABLED_CLIENTS


def _as_bytes(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    return value.encode() if isinstance(value, str) else bytes(value)


async def watch_cas(
    client: Any,
    key: str,
    token: str,
    queue: Callable[[Any], None],
) -> Optional[list]:
    """owner 校验式 CAS：WATCH key → 复读比对 token → MULTI queue(pipe) → EXEC。

    返回 EXEC 响应列表（事务已发生）；token 不匹配或 WATCH 键被并发改写
    （WatchError，事务未发生）返回 None——两种情形对调用方等价于"不是
    我的锁/状态已变"，与 Lua 版 ``GET==ARGV 则写否则 0`` 语义对齐。

    ``queue(pipe)`` 在 token 匹配后调用，向 MULTI 排入写命令（如
    ``pipe.delete(key)`` / ``pipe.pexpire(key, ttl)``）。
    """
    async with client.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(key)
            current = await pipe.get(key)
            if _as_bytes(current) != _as_bytes(token):
                await pipe.unwatch()
                return None
            pipe.multi()
            queue(pipe)
            return list(await pipe.execute())
        except WatchError:
            # 竞争改写：本次事务未提交，调用方按"未持有"处理
            return None


async def eval_or_watch(
    client: Any,
    eval_call: Callable[[], Any],
    watch_call: Callable[[], Any],
    *,
    op: str = "",
) -> Any:
    """eval 优先，命中 EVAL 禁用错误即 memo 并改走 WATCH 实现。

    memo 已标记时直接走 WATCH 路径（免每拍重复撞墙）。非禁用类错误
    （网络抖动、语法错误等）原样上抛——降级只针对"服务端拒绝脚本"。
    """
    if is_eval_disabled(client):
        return await watch_call()
    try:
        return await eval_call()
    except Exception as exc:
        if not eval_disabled_error(exc):
            raise
        mark_eval_disabled(client)
        logger.warning(
            "redis EVAL disabled, falling back to WATCH path: op=%s err=%s",
            op or "-",
            exc,
        )
        return await watch_call()


def backoff_delay(attempt: int, *, base_ms: float = 2.0, cap_ms: float = 50.0) -> float:
    """WatchError 重试退避：[0, min(base*2^attempt, cap)) ms 的 jitter 秒值。

    全抖动（full jitter）避免多实例同拍共振重试。
    """
    top = min(base_ms * (2 ** max(int(attempt), 0)), cap_ms)
    return random.uniform(0.0, top) / 1000.0


__all__ = [
    "backoff_delay",
    "eval_disabled_error",
    "eval_or_watch",
    "is_eval_disabled",
    "mark_eval_disabled",
    "watch_cas",
]
