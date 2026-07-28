import logging
from collections.abc import Awaitable, Callable

from .models import (
    INFRA_PHASES,
    MESSAGE_PHASES,
    InfraContext,
    LifecyclePhase,
    MessageContext,
    UserRequest,
)

logger = logging.getLogger(__name__)

InfraHookCallback = Callable[[InfraContext], Awaitable[None]]
MessageHookCallback = Callable[[MessageContext, UserRequest], Awaitable[None]]


class LifecycleHookRegistry:
    def __init__(self) -> None:
        self._infra_hooks: dict[
            LifecyclePhase,
            list[tuple[str, InfraHookCallback]],
        ] = {}
        self._message_hooks: dict[
            LifecyclePhase,
            list[tuple[str, MessageHookCallback]],
        ] = {}
        self._infra_global: list[tuple[str, InfraHookCallback]] = []
        self._message_global: list[tuple[str, MessageHookCallback]] = []

    def on_infra(
        self,
        phase: LifecyclePhase,
        callback: InfraHookCallback,
        *,
        feature: str = "default",
    ) -> "LifecycleHookRegistry":
        if phase not in INFRA_PHASES:
            raise ValueError(f"{phase} 不是 Infra 阶段，请使用 on_message()")
        self._infra_hooks.setdefault(phase, []).append((feature, callback))
        return self

    def on_message(
        self,
        phase: LifecyclePhase,
        callback: MessageHookCallback,
        *,
        feature: str = "default",
    ) -> "LifecycleHookRegistry":
        if phase not in MESSAGE_PHASES:
            raise ValueError(f"{phase} 不是 Message 阶段，请使用 on_infra()")
        self._message_hooks.setdefault(phase, []).append((feature, callback))
        return self

    def on_infra_all(
        self, callback: InfraHookCallback, *, feature: str = "default"
    ) -> "LifecycleHookRegistry":
        self._infra_global.append((feature, callback))
        return self

    def on_message_all(
        self, callback: MessageHookCallback, *, feature: str = "default"
    ) -> "LifecycleHookRegistry":
        self._message_global.append((feature, callback))
        return self

    def off(self, phase: LifecyclePhase, feature: str | None = None) -> None:
        bucket = self._infra_hooks if phase in INFRA_PHASES else self._message_hooks
        if phase not in bucket:
            return
        if feature is None:
            bucket.pop(phase, None)
        else:
            bucket[phase] = [(f, cb) for f, cb in bucket[phase] if f != feature]

    def off_feature(self, feature: str) -> None:
        for bucket in (self._infra_hooks, self._message_hooks):
            for phase in list(bucket.keys()):
                bucket[phase] = [(f, cb) for f, cb in bucket[phase] if f != feature]
        self._infra_global = [(f, cb) for f, cb in self._infra_global if f != feature]
        self._message_global = [
            (f, cb) for f, cb in self._message_global if f != feature
        ]

    async def emit_infra(self, ctx: InfraContext) -> None:
        for _, cb in self._infra_global:
            try:
                await cb(ctx)
            except Exception:
                logger.exception("Infra hook 执行失败: phase=%s", ctx.phase)
        for _, cb in self._infra_hooks.get(ctx.phase, []):
            try:
                await cb(ctx)
            except Exception:
                logger.exception("Infra hook 执行失败: phase=%s", ctx.phase)

    async def emit_message(self, ctx: MessageContext, req: UserRequest) -> None:
        for _, cb in self._message_global:
            try:
                await cb(ctx, req)
            except Exception:
                logger.exception("Message hook 执行失败: phase=%s", ctx.phase)
        for _, cb in self._message_hooks.get(ctx.phase, []):
            try:
                await cb(ctx, req)
            except Exception:
                logger.exception("Message hook 执行失败: phase=%s", ctx.phase)


default_hook_registry = LifecycleHookRegistry()
