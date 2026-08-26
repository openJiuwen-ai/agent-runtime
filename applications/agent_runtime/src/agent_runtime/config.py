# coding: utf-8
"""agent-runtime 应用配置（环境变量，server/local 双模式）。

框架级配置（host/port/redis/db）走 ServiceConfig.from_env()
（``OPENJIUWEN_SERVICE_*`` 环境变量，见 service/config.py）；本文件只放
本服务自有配置（``AGENT_RUNTIME_*``）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

SM_KEY_PREFIX = "session_manager"
RM_KEY_PREFIX = "resource_manager"
SERVICE_PREFIX = "/api/session"      # 唯一 App 的 prefix（端口 8091）

logger = logging.getLogger("agent_runtime.config")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        # env 手误（如 "1O"）静默回退默认值会掩盖部署配错——必须留痕
        logger.warning("invalid int env, using default: name=%s raw=%r default=%s",
                       name, os.getenv(name), default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        logger.warning("invalid float env, using default: name=%s raw=%r default=%s",
                       name, os.getenv(name), default)
        return default


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """本服务自有配置。"""

    mode: str = "server"                       # server | local（local=fake 依赖）
    kubeconfig: str | None = None              # 空 = 集群内 ServiceAccount
    default_namespace: str = "default"
    # 周期任务（秒）；SM 设计 §7 / RM 设计 §5.4 默认表
    sweep_interval: int = 1                    # SM：到期 pass + 空 Pod pass
    autoscale_interval: int = 1                # RM：min_idle 热备补位
    reclaim_interval: int = 1                  # RM：idle 超 pod_ttl 回收
    watch_interval: int = 10                   # RM：死 Pod 轮询 + 健康探测
    reconcile_interval: int = 30               # RM：孤儿对账
    # route 行为
    scope_full_timeout: float = 30.0           # scope 满（队列内）阻塞上限
    default_session_ttl: int = 60

    @classmethod
    def from_env(cls) -> "AgentRuntimeConfig":
        return cls(
            mode=os.getenv("AGENT_RUNTIME_MODE", "server").strip().lower(),
            kubeconfig=os.getenv("AGENT_RUNTIME_KUBECONFIG") or None,
            default_namespace=os.getenv("AGENT_RUNTIME_DEFAULT_NAMESPACE", "default"),
            sweep_interval=_env_int("AGENT_RUNTIME_SWEEP_INTERVAL", 1),
            autoscale_interval=_env_int("AGENT_RUNTIME_AUTOSCALE_INTERVAL", 1),
            reclaim_interval=_env_int("AGENT_RUNTIME_RECLAIM_INTERVAL", 1),
            watch_interval=_env_int("AGENT_RUNTIME_WATCH_INTERVAL", 10),
            reconcile_interval=_env_int("AGENT_RUNTIME_RECONCILE_INTERVAL", 30),
            scope_full_timeout=_env_float("AGENT_RUNTIME_SCOPE_FULL_TIMEOUT", 30.0),
            default_session_ttl=_env_int("AGENT_RUNTIME_DEFAULT_SESSION_TTL", 60),
        )
