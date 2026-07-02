# coding: utf-8
from __future__ import annotations

import sys
import types

import pytest

from channels.base import Channel, ParsedRequest
from channels.registry import (
    AdapterRegistry,
    RouteSpec,
    _extract_path_params,
    _extract_template_params,
    _load_channel,
    _match_template,
)


class _Channel(Channel):
    name = "dummy"

    def parse_request(self, body, *, path_params, headers=None, params=None):
        return ParsedRequest(
            conversation_id="conv",
            agent_id="agent",
            query="q",
            body=body,
            headers=headers or {},
            params=params or {},
            stream=True,
            trace_id="",
        )

    def build_message(self, parsed):
        return parsed

    def format_event(self, event, *, agent_id, conversation_id, elapsed):
        return event


def test_registry_register_get_and_match_routes():
    channel = _Channel()
    registry = AdapterRegistry()
    registry.register_channel("dummy", channel)
    spec = RouteSpec(
        route_key="mobile_bank",
        prefix="/v1",
        path_template="/{project_id}/agents/{agent_id}/conversations/{conversation_id}",
        channel=channel,
    )
    registry.register("mobile_bank", spec)

    assert registry.get("mobile_bank") is spec
    assert registry.get() is channel
    assert registry.get_channel("dummy") is channel
    assert registry.all_specs() == {"mobile_bank": spec}
    assert registry.match_path("/unknown") is spec

    matched, params = registry.match_route("/v1/demo/agents/a/conversations/c")
    assert matched is spec
    assert params == {"project_id": "demo", "agent_id": "a", "conversation_id": "c"}
    assert registry.match_route("/bad") == (None, {})


def test_registry_rejects_missing_keys_and_channels():
    registry = AdapterRegistry()
    with pytest.raises(ValueError):
        registry.register_channel("", _Channel())
    with pytest.raises(ValueError):
        registry.register("", RouteSpec("", "", "", _Channel()))
    with pytest.raises(KeyError):
        registry.get_channel("missing")
    with pytest.raises(KeyError):
        AdapterRegistry.from_config(
            {
                "channels": [{"name": "a", "class": "channels.mobile_bank_channel.MobileBankChannel"}],
                "routes": [{"route_key": "r", "channel": "missing"}],
            }
        )


def test_registry_from_config_loads_dynamic_channel(monkeypatch):
    module = types.ModuleType("tests.dynamic_channel")
    module.DynamicChannel = _Channel
    monkeypatch.setitem(sys.modules, "tests.dynamic_channel", module)

    registry = AdapterRegistry.from_config(
        {
            "default_route_key": "route-a",
            "channels": [{"name": "dummy", "class": "tests.dynamic_channel.DynamicChannel"}],
            "routes": [{"route_key": "route-a", "prefix": "/api", "path": "/x/{id}", "channel": "dummy"}],
        }
    )

    matched, params = registry.match_route("/api/x/123")
    assert matched is not None
    assert matched.channel.name == "dummy"
    assert params == {"id": "123"}
    assert isinstance(_load_channel("tests.dynamic_channel.DynamicChannel"), _Channel)


def test_template_helpers_cover_edge_cases():
    assert _extract_template_params("/a/1", "/a/{id}") == {"id": "1"}
    assert _extract_template_params("/a/1/extra", "/a/{id}") is None
    assert _extract_template_params("/a/1", "/b/{id}") is None
    assert _extract_template_params("/anything", "") == {}
    assert _match_template("/a/1", "/a/{id}") is True

    spec = RouteSpec("r", "/v1", "/a/{id}", _Channel())
    assert _extract_path_params("/bad/a/1", spec) is None
    assert _extract_path_params("/v1/a/1", spec) == {"id": "1"}
