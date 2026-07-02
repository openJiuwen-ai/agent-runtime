# coding: utf-8
from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dispatch import router
from channels.mobile_bank_channel import MobileBankChannel
from channels.registry import AdapterRegistry, RouteSpec


def _make_client(path_template: str = "/{project_id}/agents/{agent_id}/conversations/{conversation_id}") -> TestClient:
    app = FastAPI()
    registry = AdapterRegistry()
    registry.register(
        "mobile_bank",
        RouteSpec(
            route_key="mobile_bank",
            prefix="/v1",
            path_template=path_template,
            channel=MobileBankChannel(),
        ),
    )
    app.state.adapter_registry = registry
    app.state.executor = type("ExecutorStub", (), {"cancel_task": AsyncMock()})()
    app.include_router(router)
    return TestClient(app)


def test_cancel_route_uses_channel_path_params():
    client = _make_client()

    response = client.post("/v1/proj-1/agents/agent-1/conversations/conv-1/cancel")

    assert response.status_code == 200
    assert response.json() == {"status": "cancel_requested"}
    client.app.state.executor.cancel_task.assert_awaited_once_with("conv-1")


def test_cancel_route_supports_conv_id_alias():
    client = _make_client("/{project_id}/agents/{agent_id}/conversations/{conv_id}")

    response = client.post("/v1/proj-1/agents/agent-1/conversations/conv-alias/cancel")

    assert response.status_code == 200
    assert response.json() == {"status": "cancel_requested"}
    client.app.state.executor.cancel_task.assert_awaited_once_with("conv-alias")


def test_cancel_route_returns_404_when_channel_route_not_found():
    client = _make_client()

    response = client.post("/v1/proj-1/agents/agent-1/tasks/task-1/cancel")

    assert response.status_code == 404
    assert response.json()["error"] == "channel_route_not_found"
    client.app.state.executor.cancel_task.assert_not_awaited()
