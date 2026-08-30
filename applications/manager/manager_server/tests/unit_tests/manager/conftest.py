# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager REST API 单元测试夹具：内存 SQLite + mock Gateway push。"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from sqlalchemy.exc import SAWarning

from manager_server.infrastructure.db import get_db_handler
from manager_server.models.table_init import init_all_tables
from manager_server.routers.register import router_register

from demo_payloads import instance_create_body

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")


class _GatewayAckSimulator:
    """模拟 Gateway HTTP ack。"""

    async def gateway_request(
        self,
        jiuwenclaw_id: str,
        method: str,
        path: str,
        business: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        _ = jiuwenclaw_id
        _ = business
        _ = method
        _ = path
        return {
            "revision": "rev-ut",
            "success_flag": True,
            "result": None,
            "transport": "http",
        }


async def _open_sqlite(path: Path) -> SQLiteHandler:
    handler = SQLiteHandler(str(path))
    await handler.init_database()
    await handler.connect()
    return handler


def _require_http_ok(resp: Response) -> None:
    if resp.status_code != 200:
        raise RuntimeError(
            f"expected HTTP 200, got {resp.status_code}: {resp.text}"
        )


@dataclass
class ManagerApiHarness:
    """Manager FastAPI + SQLite 测试环境。"""

    http: AsyncClient
    handler: DBHandler
    jiuwenclaw_id: str = field(default="")
    gateway_sim: _GatewayAckSimulator = field(default_factory=_GatewayAckSimulator)

    @staticmethod
    def templates_url(path: str) -> str:
        return f"/api/v1{path}"

    @staticmethod
    def instances_url(suffix: str = "") -> str:
        if not suffix:
            return "/api/v1/instances/"
        return f"/api/v1/instances{suffix}"

    def scoped_url(self, path: str) -> str:
        if not self.jiuwenclaw_id:
            raise ValueError("jiuwenclaw_id required for instance-scoped API")
        return f"/api/v1/instances/{self.jiuwenclaw_id}{path}"

    async def create_instance(self, *, name: str = "ut-demo-instance") -> str:
        resp = await self.http.post(
            self.instances_url(),
            json=instance_create_body(jiuwenclaw_name=name),
        )
        _require_http_ok(resp)
        data = resp.json()["data"]
        self.jiuwenclaw_id = data["jiuwenclaw_id"]
        return self.jiuwenclaw_id

    async def post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.post(url, json=body)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def get_json(self, path: str, **params: Any) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.get(url, params=params or None)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def patch_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.patch(url, json=body)
        _require_http_ok(resp)
        return resp.json()["data"]

    async def delete_ok(self, path: str) -> None:
        if path.startswith((
            "/model-templates",
            "/extension-config-templates",
            "/skill-whitelist-templates",
            "/service-config-templates",
        )):
            url = self.templates_url(path)
        else:
            url = self.scoped_url(path)
        resp = await self.http.delete(url)
        _require_http_ok(resp)


# gateway_request 在各业务模块中是本地绑定，需逐一 mock
_GATEWAY_REQUEST_MODULES = (
    "manager_server.manager_config_push.client",
    "manager_server.manager_config_push",
    "manager_server.core.application_config.logging_config",
    "manager_server.core.application_config.task_memory_config",
    "manager_server.core.application_config.permissions_config",
    "manager_server.core.application_config.memory_config",
    "manager_server.core.application_config.log_masking_rule",
    "manager_server.core.template.push_template_to_gateway",
    "manager_server.core.template.push_agent_template_to_gateway",
    "manager_server.core.instance.instance_data_lifecycle",
    "manager_server.core.instance_resource.instance_agent_resource_service",
)


def _install_push_mocks(
    monkeypatch: pytest.MonkeyPatch,
    sim: _GatewayAckSimulator,
) -> None:
    for mod in _GATEWAY_REQUEST_MODULES:
        monkeypatch.setattr(
            f"{mod}.gateway_request",
            sim.gateway_request,
            raising=False,
        )

    async def _noop_require_hosts(**_kwargs: Any) -> None:
        return None

    async def _fake_runtime_identity(_host: str, **_kwargs: Any) -> dict[str, str]:
        return {"namespace": "default"}

    monkeypatch.setattr(
        "manager_server.core.instance.instance_service.require_config_hosts_reachable",
        _noop_require_hosts,
    )
    monkeypatch.setattr(
        "manager_server.core.instance.runtime_identity.fetch_runtime_identity_from_health",
        _fake_runtime_identity,
    )


@pytest_asyncio.fixture
async def manager_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """可写 SQLite + mock push 的 Manager API 客户端。"""
    sim = _GatewayAckSimulator()
    _install_push_mocks(monkeypatch, sim)

    handler = await _open_sqlite(tmp_path / "manager_ut.db")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        await init_all_tables(handler)

    app = FastAPI()
    router_register(app)

    def _override_get_db_handler() -> DBHandler:
        return handler

    app.dependency_overrides[get_db_handler] = _override_get_db_handler

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        harness = ManagerApiHarness(http=client, handler=handler, gateway_sim=sim)
        yield harness

    app.dependency_overrides.clear()
    await handler.disconnect()
