# coding: utf-8
"""评估数据采集:热路径内存缓冲 + scope 清单 + 周期采样。

两个角色:

- ``ScopeTelemetryBuffer``——route/acquire 热路径的**进程内存缓冲**(计数
  只增内存,零 Redis 往返;每副本一个 flusher 周期 5s 批量 HINCRBY 到
  ``{agent_runtime:eval}:ct:scope:{sid}``)。评估 job 是全局单副本选主,
  读不到其他副本的内存桶——**评估数据源必须 Redis 聚合**,内存只是缓冲。
- ``EvaluationCollector``——scope 清单(RM 键 ∪ 路由快照,含孤儿分类)与
  per-scope 周期采样(sys_sample job 30s 一拍,落 ZSET)。

touch 不做 per-scope 分桶:需额外 HGET session:{id} 反查 scope(热路径
加一跳),且 touch 不产生容量压力信号,评估用不上(取舍见 spec)。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

from ..util import now_ts
from .state import EvaluationState

logger = logging.getLogger("agent_runtime.evaluation")

# 缓冲里独立 scope 数上限(防御外部直写 Redis 造键;正常 scope 集由
# config_sync 全量下发,天然有界)。超限驱逐最旧 + 一次性告警。
_BUFFER_MAX_SCOPES = 1024

# flusher 周期(秒;常量不设 env——调大 eval_sample_interval 已足够降载)
FLUSH_INTERVAL_SEC = 5.0
# 单次 flush 的墙钟上限(Redis 抖动防御;超时留到下轮,计数不丢只延后)
FLUSH_TIMEOUT_SEC = 10.0

# 计数错误码 → HASH 字段(白名单:只聚合有容量语义的错误码;
# 2026-09 场景 F 快失败后 SCOPE_FULL_TIMEOUT/SCOPE_QUEUE_FULL 两码已并为 SCOPE_FULL)
_ERROR_FIELDS = {
    "SCOPE_FULL": "route_err_scope_full",
    "NO_POD_AVAILABLE": "route_err_no_pod_available",
}

# acquire outcome(归一化取首词)→ HASH 字段
_ACQUIRE_FIELDS = {
    "need_acquire": "acq_need_acquire",
    "reuse": "acq_reuse",
    "deployed": "acq_deployed",
    "follower_reuse": "acq_follower_reuse",
    "max_reached": "acq_max_reached",
    "error": "acq_error",
}

# 后台任务状态变迁事件(RM 侧直写,低频)→ HASH 字段
_EVENT_FIELDS = {
    "autoscale_deployed": "ev_autoscale_deployed",
    "autoscale_deploy_error": "ev_autoscale_deploy_error",
    "reclaimed": "ev_reclaimed",
    "pod_dead": "ev_pod_dead",
}

# scope 生效分类(visualization/评估共用;孤儿 = 仅在 RM 有 config 键)
PHASE_ACTIVE = "active"
PHASE_DISABLED = "disabled"           # scope 禁用/过期或模板禁用
PHASE_ORPHAN_RM = "orphan_rm"         # 快照无此 scope,RM config 残留
PHASE_MISSING_RM_CFG = "missing_rm_cfg"  # 快照生效但 RM 无 config 键


class ScopeTelemetryBuffer:
    """route/acquire 热路径计数缓冲(纯内存、绝不抛;drain 取走并清零)。"""

    def __init__(self, max_scopes: int = _BUFFER_MAX_SCOPES) -> None:
        self._max_scopes = max_scopes
        # OrderedDict 做 LRU:新访问挪到尾,超限驱逐头部
        self._data: OrderedDict[str, dict[str, int]] = OrderedDict()
        self._evicted_warned = False

    # -------------------------------------------------------------- 写入(热路径)

    def observe_route(self, scope_id: str, ok: bool, code: str | None) -> None:
        """route 结果计数(在 resolve 之后 scope_id 已知才调用)。"""
        try:
            bucket = self._bucket(scope_id)
            bucket["route_total"] = bucket.get("route_total", 0) + 1
            if ok:
                bucket["route_ok"] = bucket.get("route_ok", 0) + 1
            else:
                field = _ERROR_FIELDS.get(code or "", "route_err_other")
                bucket[field] = bucket.get(field, 0) + 1
        except Exception:  # noqa: BLE001 - 热路径埋点绝不反噬业务
            pass

    def observe_acquire(self, scope_id: str, outcome: str) -> None:
        """acquire 结果计数(outcome 归一化:取首词,error 路径为 "error")。"""
        try:
            key = (outcome or "error").split()[0] if outcome else "error"
            field = _ACQUIRE_FIELDS.get(key, "acq_error")
            bucket = self._bucket(scope_id)
            bucket[field] = bucket.get(field, 0) + 1
        except Exception:  # noqa: BLE001
            pass

    def _bucket(self, scope_id: str) -> dict[str, int]:
        bucket = self._data.get(scope_id)
        if bucket is None:
            if len(self._data) >= self._max_scopes:
                if not self._evicted_warned:
                    logger.warning(
                        "telemetry buffer scope cap hit (%d), evicting oldest",
                        self._max_scopes,
                    )
                    self._evicted_warned = True
                self._data.popitem(last=False)
            bucket = {}
            self._data[scope_id] = bucket
        else:
            self._data.move_to_end(scope_id)
        return bucket

    # -------------------------------------------------------------- 读取(flusher)

    def drain(self) -> dict[str, dict[str, int]]:
        """取走全部累计计数并清零(flusher 专用;失败重放由下轮覆盖)。"""
        out = {scope: dict(bucket) for scope, bucket in self._data.items()}
        self._data.clear()
        return out


class EvaluationCollector:
    """scope 清单 + 周期采样(sys_sample job 的 on_tick)。"""

    def __init__(
        self,
        *,
        eval_state: EvaluationState,
        sm_state: Any,                 # SessionState(只读访问器)
        rm_state: Any,                 # ResourceState(只读访问器)
        config_store: Any,             # ConfigStore(routing_snapshot_view)
    ) -> None:
        self.state = eval_state
        self.sm = sm_state
        self.rm = rm_state
        self.config = config_store

    # -------------------------------------------------------------- scope 清单

    async def scope_inventory(self) -> list[dict[str, Any]]:
        """RM 已知 scope ∪ 路由快照 scope 的并集清单(visualization/评估共用)。

        每行:{scope_id, phase, routing(快照定义或 None), template(快照模板或
        None), rm_config(RM config HASH 或空 dict), pods/idle/deploying,
        session_count}。phase 见模块头常量。(waiters 已随 2026-09 场景 F
        快失败拆除的等待队列移除)
        """
        snapshot = await self.config.routing_snapshot_view()
        rm_ids = set(await self.rm.known_scope_ids())
        snap_scopes = {sc.scope_id: sc for sc in snapshot.scopes}

        rows: list[dict[str, Any]] = []
        for scope_id in sorted(rm_ids | set(snap_scopes)):
            routing = snap_scopes.get(scope_id)
            template = snapshot.templates.get(routing.template_id) if routing else None
            rm_config = await self.rm.load_scope_config(scope_id) if scope_id in rm_ids else {}
            phase = self._phase(scope_id, routing, template, in_rm=scope_id in rm_ids)
            rows.append({
                "scope_id": scope_id,
                "phase": phase,
                "routing": routing,
                "template": template,
                "rm_config": rm_config,
                "pods": await self.rm.pod_count(scope_id),
                "idle": len(await self.rm.idle_pods(scope_id)),
                "deploying": await self.rm.deploying_count(scope_id),
                "session_count": await self.sm.scope_session_count(scope_id),
            })
        return rows

    @staticmethod
    def _phase(
        scope_id: str,
        routing: Any,                  # RoutingScopeDef | None
        template: Any,                 # Template | None
        *,
        in_rm: bool,
    ) -> str:
        if routing is None:
            return PHASE_ORPHAN_RM
        if not routing.is_active():
            return PHASE_DISABLED
        if template is None or not template.enabled:
            return PHASE_DISABLED
        if not in_rm:
            # 快照生效但 RM 无 config 键:config_sync 推送失败/尚未推
            return PHASE_MISSING_RM_CFG
        return PHASE_ACTIVE

    # -------------------------------------------------------------- 周期采样

    async def sample_once(self) -> None:
        """sys_sample on_tick:逐 scope 采样(池态 + 计数器快照)落 ZSET。

        计数器单调递增,相邻采样差分即速率——动态规则全从采样序列推导,
        无需独立的历史计数键。per-scope 异常隔离(照 autoscale_once 先例)。
        """
        t0 = time.monotonic()
        sampled = failed = 0
        for row in await self.scope_inventory():
            scope_id = row["scope_id"]
            try:
                counters = await self.state.read_counters(scope_id)
                record = {
                    "t": now_ts(),
                    "p": row["pods"],
                    "i": row["idle"],
                    "d": row["deploying"],
                    "s": row["session_count"],
                    "rt": counters.get("route_total", 0),
                    "ef": counters.get("route_err_scope_full", 0),
                    "en": counters.get("route_err_no_pod_available", 0),
                    "ad": counters.get("acq_deployed", 0),
                    "ar": counters.get("acq_reuse", 0),
                    "rc": counters.get("ev_reclaimed", 0),
                    "dd": counters.get("ev_pod_dead", 0),
                }
                await self.state.add_sample(scope_id, record["t"], record)
                sampled += 1
            except Exception:  # noqa: BLE001 - per-scope 隔离,下拍重试
                failed += 1
                logger.exception("eval sample failed: scope=%s", scope_id)
        logger.debug(
            "eval sample tick: sampled=%d failed=%d duration_ms=%.0f",
            sampled, failed, (time.monotonic() - t0) * 1000,
        )
