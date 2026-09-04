# coding: utf-8
"""agent-runtime 业务错误码与异常。

错误码契约见 HLD §3.1「错误响应体」：
- 过载类（SCOPE_FULL / NO_POD_AVAILABLE）带 retry_after，可重试；
- STATE_UNAVAILABLE（503）＝状态后端（Redis/DB）连接级故障，带 retry_after 可重试——
  区别于 internal 500（不可重试），LB/客户端重试语义依赖这一区分；
- CONFIG_NOT_FOUND / VALIDATION 不可重试；
- CONFIG_SYNC_BUSY（409）为串行化拒绝，可稍后重试。

Facade 间以 Python 异常传播（SM→RM / RM→SM），HTTP handler 捕获后映射为
ResponseEnvelope(ok=False, error_code, retry_after)；HTTP 状态码经框架
``register_http_status`` 注册（在包 ``__init__`` / main 里完成，见 ``register_codes()``）。
"""

from __future__ import annotations

from openjiuwen_runtime.service.errors import FrameworkError, register_http_status

# ---------------------------------------------------------------- 错误码常量


class ErrorCode:
    """agent-runtime 业务错误码（字符串，序列化进 ResponseEnvelope.error_code）。"""

    # SM 对外
    SCOPE_FULL = "SCOPE_FULL"                      # 503：scope 满，立即快失败
    NO_POD_AVAILABLE = "NO_POD_AVAILABLE"          # 503：acquire 失败（封顶/部署失败）
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"          # 503：resolve 无匹配配置
    VALIDATION = "VALIDATION"                      # 400：参数错
    CONFIG_SYNC_BUSY = "CONFIG_SYNC_BUSY"          # 409：上一次配置热更新未完成
    STATE_UNAVAILABLE = "STATE_UNAVAILABLE"        # 503：状态后端（Redis/DB）连接级故障
    # RM Facade 异常（SM route 捕获后统一映射 NO_POD_AVAILABLE）
    MAX_PODS_REACHED = "MAX_PODS_REACHED"
    DEPLOY_FAILED = "DEPLOY_FAILED"


# 业务错误码 → HTTP 状态（HLD §3.1）。VALIDATION 复用框架小写码即可，这里注册大写契约码。
HTTP_STATUS_MAP = {
    ErrorCode.SCOPE_FULL: 503,
    ErrorCode.NO_POD_AVAILABLE: 503,
    ErrorCode.CONFIG_NOT_FOUND: 503,
    ErrorCode.VALIDATION: 400,
    ErrorCode.CONFIG_SYNC_BUSY: 409,
    ErrorCode.STATE_UNAVAILABLE: 503,
    ErrorCode.MAX_PODS_REACHED: 503,
    ErrorCode.DEPLOY_FAILED: 503,
}


def register_codes() -> None:
    """把业务错误码的 HTTP 状态映射注册进框架（幂等，import/启动时调用一次）。"""
    register_http_status(HTTP_STATUS_MAP)


# 过载响应建议重试间隔（秒）。原与队列等待参数同住 orchestrator，2026-09
# 场景 F 快失败改造后归属错误契约（三个过载类的默认值）。
DEFAULT_RETRY_AFTER = 1


# ---------------------------------------------------------------- 异常基类


class AgentRuntimeError(FrameworkError):
    """agent-runtime 业务异常基类。``retry_after`` 仅过载类携带（秒）。"""

    retry_after: int | None = None

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message, code=getattr(type(self), "code", ErrorCode.VALIDATION))
        if retry_after is not None:
            self.retry_after = int(retry_after)


# ---- SM 对外


class ScopeFull(AgentRuntimeError):
    """scope 满（闸门：scope_concurrency 或 max_pods×pod_concurrency 预算）。

    场景 F 快失败（2026-09）：不排队、不订阅，立即 503 + retry_after；背压
    责任在 gateway（SM 设计 §8.3 指数退避契约不变）。有界等待已整体拆除，
    见 docs/feature/2026-09-scope-full-fastfail.md。
    """

    code = ErrorCode.SCOPE_FULL


class NoPodAvailable(AgentRuntimeError):
    code = ErrorCode.NO_POD_AVAILABLE


class ConfigNotFound(AgentRuntimeError):
    code = ErrorCode.CONFIG_NOT_FOUND


class InvalidParams(AgentRuntimeError):
    code = ErrorCode.VALIDATION


class ConfigSyncBusy(AgentRuntimeError):
    code = ErrorCode.CONFIG_SYNC_BUSY


class StateUnavailable(AgentRuntimeError):
    """Redis/DB 连接级故障（handler 层翻译，见 handlers._INFRA_EXCEPTIONS）。"""

    code = ErrorCode.STATE_UNAVAILABLE


# ---- RM Facade（进程内异常，SM 捕获映射）


class MaxPodsReached(AgentRuntimeError):
    code = ErrorCode.MAX_PODS_REACHED


class DeployFailed(AgentRuntimeError):
    code = ErrorCode.DEPLOY_FAILED
