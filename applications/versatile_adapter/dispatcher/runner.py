# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileAdapterRunner — 配置驱动的动态路由层。

从 YAML 配置文件加载 adapter 定义，在 run_async 时根据 target 动态匹配并创建适配器：
  - target 含 workflow_id 且匹配到 workflow 配置 → VersatileWorkflow
  - target 含 intent 且匹配到 workflow 配置的 intent → VersatileWorkflow
  - 否则 → VersatileController（使用第一个 type=controller 的配置）
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator, Optional

import yaml
from loguru import logger

from adapters.versatile_controller import VersatileController
from adapters.versatile_workflow import VersatileWorkflow
from config import get_settings
from event.events import AdapterEvent


# 配置文件加载优先级：
#   1. VersatileAdapterRunner(config_path=...) 显式参数
#   2. 环境变量 VERSATILE_PROXY_CONFIG_PATH
#   3. 部署默认路径 /etc/edpagent/config/versatile_proxy.yaml
#   4. 当前 versatile_adapter 目录下的 versatile_proxy.yaml（便于本地开发）
_DEPLOY_DEFAULT_CONFIG_PATH = Path("/etc/edpagent/config/versatile_proxy.yaml")
_LOCAL_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "versatile_proxy.yaml"


def _resolve_default_config_path() -> Path:
    env_path = os.environ.get("VERSATILE_PROXY_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    if _DEPLOY_DEFAULT_CONFIG_PATH.exists():
        return _DEPLOY_DEFAULT_CONFIG_PATH
    return _LOCAL_DEFAULT_CONFIG_PATH


class _VersatileAdapterConfig:
    """单个 adapter 的配置。"""

    __slots__ = (
        "name", "type", "url_template", "timeout",
        "headers_template", "forward_header_whitelist",
        "workflow_result_node", "workflow_id", "intent",
    )

    def __init__(self, raw: dict) -> None:
        self.name = raw["name"]
        self.type = raw["type"]
        self.url_template = raw["url_template"]
        self.timeout = raw.get("timeout", 600)
        self.headers_template = raw.get("headers_template", {})
        self.forward_header_whitelist = (
            set(h.lower() for h in raw["forward_header_whitelist"])
            if "forward_header_whitelist" in raw else None
        )
        self.workflow_result_node = raw.get("workflow_result_node")
        self.workflow_id = raw.get("workflow_id")
        self.intent = raw.get("intent")


class VersatileAdapterRunner:
    """配置驱动的动态路由 Runner。"""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        path = config_path or _resolve_default_config_path()
        self._adapters = self._load_config(path)
        if not self._adapters:
            self._adapters = self._build_from_settings()
        self._controller_cfg = self._find_controller_cfg()
        logger.info(
            f"[VersatileAdapterRunner] 加载 {len(self._adapters)} 个 adapter 配置"
            f"（controller={self._controller_cfg.name if self._controller_cfg else '(none)'}）"
        )

    @staticmethod
    def _load_config(path: Path) -> list[_VersatileAdapterConfig]:
        if not path.exists():
            logger.warning(f"[VersatileAdapterRunner] 配置文件不存在: {path}，将从 Settings 生成")
            return []
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        adapters_raw = raw.get("adapters", [])
        return [_VersatileAdapterConfig(b) for b in adapters_raw]

    @staticmethod
    def _build_from_settings() -> list[_VersatileAdapterConfig]:
        """YAML 配置文件不存在时，从 Settings 自动生成唯一的 controller 配置。"""
        settings = get_settings()
        raw = {
            "name": "default_controller",
            "type": "controller",
            "url_template": settings.versatile_url_template or "",
            "timeout": settings.versatile_timeout or 600,
            "headers_template": dict(settings.versatile_headers_template)
                if settings.versatile_headers_template else {},
            "workflow_result_node": settings.versatile_workflow_result_node,
        }
        logger.info(f"[VersatileAdapterRunner] 从 Settings 自动生成 default_controller 配置: {raw}")
        return [_VersatileAdapterConfig(raw)]

    def _find_controller_cfg(self) -> Optional[_VersatileAdapterConfig]:
        for b in self._adapters:
            if b.type == "controller":
                return b
        return None

    def _match_workflow(self, target: dict) -> Optional[_VersatileAdapterConfig]:
        """根据 target 中的 workflow_id 或 intent 匹配 workflow 配置。"""
        for b in self._adapters:
            if b.type != "workflow":
                continue
            if b.workflow_id and target.get("workflow_id") == b.workflow_id:
                return b
            if b.intent and target.get("intent") == b.intent:
                return b
        return None

    @staticmethod
    def _create_adapter(cfg: _VersatileAdapterConfig):
        """根据配置创建对应的适配器实例。"""
        whitelist = cfg.forward_header_whitelist
        if cfg.type == "workflow":
            return VersatileWorkflow(
                url_template=cfg.url_template,
                workflow_id=cfg.workflow_id or "",
                timeout=cfg.timeout,
                headers_template=cfg.headers_template,
                forward_header_whitelist=whitelist,
            )
        return VersatileController(
            url_template=cfg.url_template,
            timeout=cfg.timeout,
            headers_template=cfg.headers_template,
            forward_header_whitelist=whitelist,
            workflow_result_node=cfg.workflow_result_node,
        )

    async def run_async(self, target: dict, headers: dict, params: dict,
                        body: dict) -> AsyncGenerator[AdapterEvent, None]:
        """根据 target 动态匹配配置并创建适配器，驱动流。"""
        conv_id = target.get("conversation_id", "")
        wf_cfg = self._match_workflow(target)
        cfg = wf_cfg or self._controller_cfg
        if cfg is None:
            raise ValueError(f"无法匹配 adapter 配置: target={target}")

        logger.debug(f"[VersatileAdapterRunner] target 匹配 adapter={cfg.name} ({cfg.type})")
        adapter = self._create_adapter(cfg)

        async for event in adapter.dispatch_stream(conv_id, headers=headers, params=params, body=body):
            yield event
