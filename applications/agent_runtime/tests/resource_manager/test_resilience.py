# coding: utf-8
"""RM 韧性测试:异常生命周期路径(缺陷⑤回归网)。

2026-08-26 真环境实测教训:优雅停机取消在飞的 autoscale deploy tick 时,
``except Exception`` 接不住 ``CancelledError``(BaseException),deploying 占位
不清 → 泄漏占位计入 max_pods 把池永久堵死(route 恒 NO_POD_AVAILABLE)。
红线「错误路径必须清占位」必须覆盖取消路径。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator
from agent_runtime.resource_manager.sweeper import ResourceSweeper
from tests.conftest import requires_lua

SCOPE = "scope-resilience"


class _SlowK8s:
    """包装 FakeK8s:deploy 人为延迟(制造取消窗口),其余转发。"""

    def __init__(self, inner, deploy_delay: float) -> None:
        self._inner = inner
        self.deploy_delay = deploy_delay
        self.in_deploy = asyncio.Event()
        self.deploys = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def deploy(self, pod_spec):
        self.deploys += 1
        self.in_deploy.set()
        await asyncio.sleep(self.deploy_delay)     # 取消窗口
        return await self._inner.deploy(pod_spec)


@pytest.fixture
def nat_cfg():
    """合法 pod_spec(autoscale 预热依赖 pod_spec_json 非空)。"""
    return {
        "agent_image": "agentserver:1.0", "namespace": "default",
        "sse_port": 8080, "sse_path": "/sse",
    }


@requires_lua
async def test_shutdown_cancel_mid_deploy_clears_placeholder(rm_state, k8s, nat_cfg):
    """部署中途取消(优雅停机语义)→ deploying 占位必须清(红线),不泄漏。"""
    slow = _SlowK8s(k8s, deploy_delay=0.5)
    sweeper = ResourceSweeper(
        rm_state, slow, orchestrator=ResourceOrchestrator(rm_state, slow),
        sm_facade=None,
    )
    await rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
        "deploy_ver": "ver1", "pod_spec_json": json.dumps(nat_cfg),
    })

    task = asyncio.create_task(sweeper.autoscale_once())
    await asyncio.wait_for(slow.in_deploy.wait(), timeout=2)   # 进入 deploy 内
    task.cancel()                                              # 模拟停机取消
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await rm_state.deploying_count(SCOPE) == 0, "取消路径泄漏 deploying 占位"
    assert await rm_state.idle_pods(SCOPE) == []                # 未注册进池
    # 锁与占位都不留 → 后续 autoscale 可正常补位(池未被堵死)
    assert await rm_state.redis.exists(rm_state.k.lock_deploy(SCOPE)) == 0


@requires_lua
async def test_redeploy_after_cancel_succeeds(rm_state, k8s, nat_cfg):
    """取消后下一拍 autoscale 正常完成(池自愈,占位不残留导致的 max 堵死不存在)。"""
    slow = _SlowK8s(k8s, deploy_delay=0.5)
    orchestrator = ResourceOrchestrator(rm_state, slow)
    sweeper = ResourceSweeper(rm_state, slow, orchestrator=orchestrator,
                              sm_facade=None)
    await rm_state.save_scope_config(SCOPE, {
        "min_idle_pods": 1, "max_pods": 2, "pod_ttl": 300,
        "deploy_ver": "ver1", "pod_spec_json": json.dumps(nat_cfg),
    })

    first = asyncio.create_task(sweeper.autoscale_once())
    await asyncio.wait_for(slow.in_deploy.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    slow.deploy_delay = 0                       # 第二拍立即完成
    await sweeper.autoscale_once()
    assert len(await rm_state.idle_pods(SCOPE)) == 1            # 热备入池
    assert await rm_state.deploying_count(SCOPE) == 0
    assert await rm_state.pod_count(SCOPE) == 1
