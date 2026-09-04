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

# 键前缀带 Redis Cluster hash tag（{xxx}）：cluster 下模块键域同槽，多键 Lua
# 保持原子；单实例/哨兵下 {} 无语义。须与两模块 state.KEY_PREFIX 一致。
SM_KEY_PREFIX = "{session_manager}"
RM_KEY_PREFIX = "{resource_manager}"
SERVICE_PREFIX = "/api/session"      # 唯一 App 的 prefix（端口 8091）

logger = logging.getLogger("agent_runtime.config")


def _env_float(name: str, default: float) -> float:
    """float 型 env(评估 LLM 超时用;场景 F 快失败时随 scope_full_timeout
    被删,评估层引入后恢复)。"""
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        logger.warning("invalid float env, using default: name=%s raw=%r default=%s",
                       name, os.getenv(name), default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        # env 手误（如 "1O"）静默回退默认值会掩盖部署配错——必须留痕
        logger.warning("invalid int env, using default: name=%s raw=%r default=%s",
                       name, os.getenv(name), default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


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
    default_session_ttl: int = 60
    # 系统自评估（docs/spec/evaluation.md）：采样/评估两 job + LLM 分析（默认禁用）
    eval_sample_interval: int = 30             # sys_sample：趋势采样间隔（下限钳 5s）
    eval_interval: int = 300                   # sys_eval：评估报告间隔（下限钳 30s）
    eval_llm_base_url: str = ""                # OpenAI 兼容端点；与 model 均非空才启用
    eval_llm_api_key: str = ""                 # 可空（内网免鉴权端点）；绝不进日志/报告
    eval_llm_model: str = ""
    eval_llm_timeout: float = 60.0             # 须 < TICK_TIMEOUTS.sys_eval
    eval_llm_disable_thinking: bool = False    # 推理模型(GLM 等)关思考：reasoning
                                               # 计入 max_tokens 预算会吃空
                                               # content；vLLM chat_template_kwargs
                                               # 开关，非 vLLM 端点勿开
    eval_llm_max_tokens: int = 1024            # 推理模型预算须盖住 reasoning+答案
                                               # （实测 GLM-5.3 需 ~16k；常规模型
                                               # 默认值够）
    eval_pod_budget: int = 0                   # 集群 AgentServer Pod 预算；0=预算规则关闭

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
            default_session_ttl=_env_int("AGENT_RUNTIME_DEFAULT_SESSION_TTL", 60),
            eval_sample_interval=_env_int("AGENT_RUNTIME_EVAL_SAMPLE_INTERVAL", 30),
            eval_interval=_env_int("AGENT_RUNTIME_EVAL_INTERVAL", 300),
            eval_llm_base_url=(os.getenv("AGENT_RUNTIME_EVAL_LLM_BASE_URL") or "").strip(),
            eval_llm_api_key=os.getenv("AGENT_RUNTIME_EVAL_LLM_API_KEY") or "",
            eval_llm_model=(os.getenv("AGENT_RUNTIME_EVAL_LLM_MODEL") or "").strip(),
            eval_llm_timeout=_env_float("AGENT_RUNTIME_EVAL_LLM_TIMEOUT", 60.0),
            eval_llm_disable_thinking=_env_bool(
                "AGENT_RUNTIME_EVAL_LLM_DISABLE_THINKING"),
            eval_llm_max_tokens=_env_int(
                "AGENT_RUNTIME_EVAL_LLM_MAX_TOKENS", 1024),
            eval_pod_budget=_env_int("AGENT_RUNTIME_EVAL_POD_BUDGET", 0),
        )
