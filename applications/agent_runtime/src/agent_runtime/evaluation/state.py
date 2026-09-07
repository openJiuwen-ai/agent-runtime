# coding: utf-8
"""评估域 Redis 键 schema + 状态门面(对齐 SM/RM 的 state 门面模式)。

前缀 ``{agent_runtime:eval}`` = Redis Cluster **hash tag**:eval 全部键落同一
slot;单实例/哨兵/fakeredis 下 ``{}`` 无语义,同一套键名兼容两种部署
(与 ``{session_manager}``/``{resource_manager}`` 同款双形态,config.py 注释同源)。

**只用单键命令、零 Lua**——不存在跨槽 EVAL 问题;scope_id 经 SCOPE_ID_RE
校验禁 ``{``/``}``,不会破坏 tag。

键表:

| 键 | 类型 | TTL | 语义 |
|---|---|---|---|
| ``sample:scope:{sid}`` | ZSET(member=含 t 的紧凑 JSON,score=t 秒) | 25h(每采样刷新) | per-scope 历史趋势采样(sys_sample 30s 写) |
| ``ct:scope:{sid}`` | HASH(计数字段) | 25h(每次写刷新) | per-scope 全副本聚合请求/事件计数 |
| ``report:latest`` | STRING(报告 JSON) | 无 TTL | 最近一份评估报告 |
| ``report:history`` | ZSET(member=报告瘦身 JSON,score=ts) | 30d(保最近 200) | 评估报告历史 |

scope 停止被采样/计数后键自然过期,免残留清理。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..util import s, to_int

# hash tag 语义见模块 docstring(须与 config.py 的 EVAL_KEY_PREFIX 一致)
KEY_PREFIX = "{agent_runtime:eval}"

# 键 TTL:采样窗口 24h + 1h 余量(每写刷新;scope 消失后自然过期)
SAMPLE_TTL_SEC = 25 * 3600
# 报告历史保留(30d TTL + 容量 200 双保险:ZSET 体积有界,TTL 兜底)
REPORT_TTL_SEC = 30 * 24 * 3600
REPORT_HISTORY_MAX = 200

logger = logging.getLogger("agent_runtime.evaluation")


class EvalKeys:
    """键名拼装(scope_id 经 SCOPE_ID_RE 保障不含 : 之外的键段分隔符冲突)。"""

    @staticmethod
    def scope_samples(scope_id: str) -> str:
        return f"{KEY_PREFIX}:sample:scope:{scope_id}"

    @staticmethod
    def scope_counters(scope_id: str) -> str:
        return f"{KEY_PREFIX}:ct:scope:{scope_id}"

    @staticmethod
    def report_latest() -> str:
        return f"{KEY_PREFIX}:report:latest"

    @staticmethod
    def report_history() -> str:
        return f"{KEY_PREFIX}:report:history"


class EvaluationState:
    """评估域 Redis 门面(全单键命令;异常上抛由调用方 per-scope 隔离)。"""

    def __init__(self, redis: Any) -> None:
        self.redis = redis
        self.k = EvalKeys()

    # -------------------------------------------------------------- 计数(全副本聚合)

    async def bump_counters(self, scope_id: str, deltas: dict[str, int]) -> None:
        """批量自增 per-scope 计数 HASH(HINCRBY 幂等可交换,多副本双写安全;
        flusher 已按 5s 批量摊薄,逐字段自增的往返数有界)。"""
        if not deltas:
            return
        key = self.k.scope_counters(scope_id)
        for field, value in deltas.items():
            if value:
                await self.redis.hincrby(key, field, int(value))
        await self.redis.expire(key, SAMPLE_TTL_SEC)

    async def read_counters(self, scope_id: str) -> dict[str, int]:
        raw = await self.redis.hgetall(self.k.scope_counters(scope_id))
        return {s(f): to_int(v) for f, v in raw.items()}

    async def bump_event(self, scope_id: str, field: str, by: int = 1) -> None:
        """单个事件计数自增(RM 后台任务状态变迁直写;低频,直写无缓冲)。"""
        if not scope_id:
            return
        await self.bump_counters(scope_id, {field: int(by)})

    # -------------------------------------------------------------- 趋势采样

    async def add_sample(
        self, scope_id: str, ts: int, record: dict[str, Any]
    ) -> None:
        """一条采样(member 含 t 保证唯一;score=t 秒)。"""
        key = self.k.scope_samples(scope_id)
        member = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        await self.redis.zadd(key, {member: float(ts)})
        await self.redis.expire(key, SAMPLE_TTL_SEC)

    async def samples(
        self, scope_id: str, since_ts: float, limit: int = 2880
    ) -> list[dict[str, Any]]:
        """窗口内采样(升序返回;超限保留最新的 limit 条)。"""
        raw = await self.redis.zrangebyscore(
            self.k.scope_samples(scope_id), since_ts, "+inf"
        )
        members = [s(m) for m in raw][-limit:]
        out: list[dict[str, Any]] = []
        for member in members:
            try:
                out.append(json.loads(member))
            except ValueError:
                logger.warning("eval sample corrupt, skipped: scope=%s", scope_id)
        return out

    # -------------------------------------------------------------- 评估报告

    async def write_report(self, report: dict[str, Any]) -> None:
        """latest 原子 SET + history ZADD(瘦身条目)+ 容量裁剪 + TTL 刷新。"""
        text = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        ts = to_int(report.get("generated_at"))
        await self.redis.set(self.k.report_latest(), text)
        history_key = self.k.report_history()
        entry = {
            "generated_at": report.get("generated_at"),
            "instance_id": report.get("instance_id"),
            "llm": report.get("llm"),
            "summary": report.get("summary"),
        }
        member = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        await self.redis.zadd(history_key, {member: float(ts)})
        await self.redis.zremrangebyrank(history_key, 0, -REPORT_HISTORY_MAX - 1)
        await self.redis.expire(history_key, REPORT_TTL_SEC)

    async def latest_report(self) -> dict[str, Any] | None:
        raw = await self.redis.get(self.k.report_latest())
        if not raw:
            return None
        try:
            value = json.loads(s(raw))
        except ValueError:
            logger.warning("eval report latest corrupt, ignoring")
            return None
        return value if isinstance(value, dict) else None

    async def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """报告历史(新在前,瘦身条目)。"""
        raw = await self.redis.zrevrange(self.k.report_history(), 0, limit - 1)
        out: list[dict[str, Any]] = []
        for member in raw:
            try:
                value = json.loads(s(member))
            except ValueError:
                logger.warning("eval report history corrupt, skipped")
                continue
            if isinstance(value, dict):
                out.append(value)
        return out
