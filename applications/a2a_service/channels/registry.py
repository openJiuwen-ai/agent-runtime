from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .base import Channel


@dataclass(frozen=True)
class RouteSpec:
    route_key: str
    prefix: str
    path_template: str
    channel: Channel


class AdapterRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}
        self._specs: dict[str, RouteSpec] = {}
        self._default_route_key = "mobile_bank"

    def register_channel(self, name: str, channel: Channel) -> None:
        if not name:
            raise ValueError("channel name is required")
        self._channels[name] = channel

    def register(self, route_key: str, spec: RouteSpec) -> None:
        if not route_key:
            raise ValueError("route_key is required")
        self._specs[route_key] = spec
        channel_name = getattr(spec.channel, "name", route_key) or route_key
        self._channels.setdefault(channel_name, spec.channel)

    def get(self, route_key: str | None = None) -> RouteSpec | Channel:
        """Return RouteSpec by route_key; no args keeps legacy default Channel behavior."""
        if route_key is None:
            return self.get_channel()
        return self._specs.get(route_key)

    def get_channel(self, route_key: str | None = None) -> Channel:
        key = route_key or self._default_route_key
        spec = self._specs.get(key)
        if spec is not None:
            return spec.channel
        if key in self._channels:
            return self._channels[key]
        raise KeyError(f"route/channel not registered: {key}")

    def all_specs(self) -> dict[str, RouteSpec]:
        return dict(self._specs)

    def match_path(self, path: str) -> RouteSpec | None:
        normalized_path = path or ""
        for spec in self._specs.values():
            if _extract_path_params(normalized_path, spec) is not None:
                return spec
        return self._specs.get(self._default_route_key)

    def match_route(self, path: str) -> tuple[RouteSpec | None, dict[str, str]]:
        """Strictly match a request path and extract template variables.

        Unlike match_path(), this does not fall back to the default route. HTTP
        entrypoints should use this method so unknown paths fail fast.
        """
        normalized_path = path or ""
        for spec in self._specs.values():
            params = _extract_path_params(normalized_path, spec)
            if params is not None:
                return spec, params
        return None, {}

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "AdapterRegistry":
        registry = cls()
        data = config or {}

        default_route_key = (
            data.get("default_route_key")
            or data.get("default_channel")
            or "mobile_bank"
        )
        registry._default_route_key = str(default_route_key)

        channel_instances: dict[str, Channel] = {}
        channels = data.get("channels") or []
        if not channels:
            channels = [
                {
                    "name": "mobile_bank",
                    "class": "channels.mobile_bank_channel.MobileBankChannel",
                }
            ]

        for item in channels:
            name = str(item["name"])
            channel = _load_channel(str(item["class"]))
            registry.register_channel(name, channel)
            channel_instances[name] = channel

        routes = data.get("routes")
        if routes is None:
            default_channel = str(data.get("default_channel") or default_route_key)
            routes = [
                {
                    "route_key": default_route_key,
                    "prefix": "/v1",
                    "path_template": "/{project_id}/agents/{agent_id}/conversations/{conversation_id}",
                    "channel": default_channel,
                }
            ]

        for item in routes:
            route_key = str(item["route_key"])
            channel_name = str(item.get("channel") or route_key)
            channel = channel_instances.get(channel_name) or registry._channels.get(channel_name)
            if channel is None:
                raise KeyError(f"channel not registered for route {route_key}: {channel_name}")
            registry.register(
                route_key,
                RouteSpec(
                    route_key=route_key,
                    prefix=str(item.get("prefix") or ""),
                    path_template=str(item.get("path_template") or item.get("path") or ""),
                    channel=channel,
                ),
            )

        return registry

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AdapterRegistry":
        config_path = Path(path)
        data: dict[str, Any] = {}
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return cls.from_config(data)


def _load_channel(class_path: str) -> Channel:
    module_name, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    channel_cls = getattr(module, class_name)
    return channel_cls()


def _match_template(path: str, template: str) -> bool:
    return _extract_template_params(path, template) is not None


def _extract_path_params(path: str, spec: RouteSpec) -> dict[str, str] | None:
    if spec.prefix and not path.startswith(spec.prefix):
        return None

    candidates = [path]
    if spec.prefix:
        stripped = path[len(spec.prefix):] or "/"
        candidates.insert(0, stripped)

    for candidate in candidates:
        params = _extract_template_params(candidate, spec.path_template)
        if params is not None:
            return params
    return None


def _extract_template_params(path: str, template: str) -> dict[str, str] | None:
    if not template:
        return {}
    path_parts = [part for part in path.strip("/").split("/") if part]
    template_parts = [part for part in template.strip("/").split("/") if part]
    if len(path_parts) != len(template_parts):
        return None
    params: dict[str, str] = {}
    for actual, expected in zip(path_parts, template_parts):
        if expected.startswith("{") and expected.endswith("}"):
            params[expected[1:-1]] = actual
            continue
        if actual != expected:
            return None
    return params
