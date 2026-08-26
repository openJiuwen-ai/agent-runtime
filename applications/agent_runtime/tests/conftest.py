# coding: utf-8
"""pytest 共享 fixtures：fakeredis + SM/RM 状态门面。

fakeredis 注意事项（CLAUDE.md 陷阱清单）：
- pubsub 需共享同一 FakeRedis 实例（本文件所有门面共享同一 client）；
- EVAL 内 PUBLISH 依赖 lupa（fakeredis[lua]）；缺失时相关用例自动 skip。
"""

from __future__ import annotations

import pytest
from fakeredis.aioredis import FakeRedis

from agent_runtime.resource_manager.state import ResourceState
from agent_runtime.session_manager.state import SessionState

HAS_LUA = True
try:
    import lupa  # noqa: F401
except ImportError:
    HAS_LUA = False

requires_lua = pytest.mark.skipif(
    not HAS_LUA, reason="fakeredis Lua 脚本支持需要 lupa（pip install fakeredis[lua]）"
)


@pytest.fixture
async def redis_client():
    client = FakeRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def sm_state(redis_client) -> SessionState:
    return SessionState(redis_client)


@pytest.fixture
def rm_state(redis_client) -> ResourceState:
    return ResourceState(redis_client)


@pytest.fixture
async def db_handler(tmp_path):
    """文件型 SQLite（:memory: 在 SQLAlchemy 连接池下会丢表）。"""
    from openjiuwen_runtime.foundation.db import SQLiteHandler

    from agent_runtime.session_manager.config_store import (
        ROUTING_SCOPE_TABLE_DEF,
        SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    )

    handler = SQLiteHandler(str(tmp_path / "test.db"))
    await handler.connect()
    await handler.init_table(SERVICE_CONFIG_TEMPLATE_TABLE_DEF)
    await handler.init_table(ROUTING_SCOPE_TABLE_DEF)
    yield handler
    await handler.disconnect()


class Runtime:
    """组件全链路装配：SM（orchestrator/sweeper/facade/config_store）+ RM
    （orchestrator/sweeper/facade）+ FakeK8s，共享一个 fakeredis。"""

    def __init__(self, db, redis_client, k8s, *, scope_full_timeout: float = 30.0):
        from agent_runtime.resource_manager.facade import ResourceManagerFacade
        from agent_runtime.resource_manager.orchestrator import ResourceOrchestrator
        from agent_runtime.resource_manager.sweeper import ResourceSweeper
        from agent_runtime.session_manager.config_store import ConfigStore
        from agent_runtime.session_manager.facade import SessionManagerFacade
        from agent_runtime.session_manager.orchestrator import SessionOrchestrator
        from agent_runtime.session_manager.sweeper import SessionSweeper

        self.redis = redis_client
        self.k8s = k8s
        self.sm_state = SessionState(redis_client)
        self.rm_state = ResourceState(redis_client)

        self.sm_facade = SessionManagerFacade(self.sm_state)
        self.rm_orchestrator = ResourceOrchestrator(self.rm_state, k8s)
        self.rm_facade = ResourceManagerFacade(self.rm_orchestrator)
        # 池参数推送记录（断言 config_sync 是否推 RM 用）
        self.pool_pushes: list[tuple[str, dict, dict | None]] = []

        async def _push(scope_id, pool, pod_spec):
            self.pool_pushes.append((scope_id, pool, pod_spec))
            await self.rm_facade.update_pool_config(scope_id, pool, pod_spec)

        self.config_store = ConfigStore(db, self.sm_state, push_pool_config=_push)
        self.orchestrator = SessionOrchestrator(
            self.sm_state, self.config_store, self.rm_facade,
            scope_full_timeout=scope_full_timeout,
        )
        self.sm_sweeper = SessionSweeper(self.sm_state, self.rm_facade)
        self.rm_sweeper = ResourceSweeper(
            self.rm_state, k8s, self.sm_facade,
            orchestrator=self.rm_orchestrator,
        )

    async def seed_template(self, template_id="tpl-1", scope_id="scope-main",
                            **overrides) -> None:
        """全量下发一个 template + 一个通配兜底 scope（空 routing_rules）。"""
        template = {
            "agent_image": "agentserver:1.0",
            "namespace": "default",
            "scope_concurrency": 3,
            "pod_concurrency": 2,
            "session_ttl": 60,
            "pod_ttl": 300,
            "min_idle_pods": 0,
            **overrides,
        }
        await self.config_store.config_sync({
            "templates": [{"template_id": template_id, **template}],
            "scopes": [{"scope_id": scope_id, "index": 0,
                        "template_id": template_id, "routing_rules": []}],
        })

    async def route(self, session_id, group_id="grp", bot_id="bot",
                    user_id="user", request_id=None):
        return await self.orchestrator.route(
            request_id=request_id or f"req-{session_id}",
            session_id=session_id, group_id=group_id, bot_id=bot_id,
            user_id=user_id,
        )


@pytest.fixture
def k8s():
    from agent_runtime.resource_manager.k8s import FakeK8sPodClient

    return FakeK8sPodClient()


@pytest.fixture
def runtime(db_handler, redis_client, k8s):
    return Runtime(db_handler, redis_client, k8s)
