# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""SubAgentEntry 配置字段测试（不依赖 _helpers，可独立运行）。

测试新增的 a2a_gateway_base/agent_card_name/token 字段。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

# 直接从文件路径加载 config.py（绕过 agents.EDPAgent 占位注入）
_cfg_path = Path(__file__).resolve().parents[2] / "agents" / "EDPAgent" / "config.py"
_spec = importlib.util.spec_from_file_location("_edp_config", _cfg_path)
_cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cfg)
SubAgentEntry = _cfg.SubAgentEntry
SubAgentsConfig = _cfg.SubAgentsConfig
# importlib 加载时 pydantic forward ref 未解析，需手动 rebuild
SubAgentsConfig.model_rebuild()


class TestSubAgentEntryFields:
    """测试 SubAgentEntry 新增的 a2a_gateway_base/agent_card_name/token 字段。"""

    @staticmethod
    def test_default_values():
        """默认值正确。"""
        entry = SubAgentEntry()
        assert entry.entity_type == "default"
        assert entry.url == ""
        assert entry.name == "SubEDPAgent"
        assert entry.endpoint_type == "direct"
        assert entry.a2a_gateway_base == ""
        assert entry.agent_card_name == "SubDPA"
        assert entry.token == ""

    @staticmethod
    def test_gateway_fields_settable():
        """可设置网关相关字段。"""
        entry = SubAgentEntry(
            entity_type="ZDT",
            endpoint_type="gateway",
            a2a_gateway_base="https://a2a-gateway.example.com",
            agent_card_name="SubEDPAgent",
            token="sub-agent-token",
            url="",
            name="zdt_agent",
        )
        assert entry.endpoint_type == "gateway"
        assert entry.a2a_gateway_base == "https://a2a-gateway.example.com"
        assert entry.agent_card_name == "SubEDPAgent"
        assert entry.token == "sub-agent-token"

    @staticmethod
    def test_yaml_round_trip():
        """yaml 配置可正确解析为 SubAgentEntry。"""
        yaml_data = {
            "sub_agents": [
                {
                    "entity_type": "ZDT",
                    "endpoint_type": "gateway",
                    "a2a_gateway_base": "https://a2a-gateway.example.com",
                    "agent_card_name": "SubEDPAgent",
                    "token": "sub-agent-token",
                    "url": "",
                    "name": "zdt_agent",
                }
            ]
        }
        config = SubAgentsConfig.model_validate(yaml_data)
        entry = config.sub_agents[0]
        assert entry.endpoint_type == "gateway"
        assert entry.agent_card_name == "SubEDPAgent"
        assert entry.a2a_gateway_base == "https://a2a-gateway.example.com"
        assert entry.token == "sub-agent-token"

    @staticmethod
    def test_direct_mode_default():
        """不配 endpoint_type 时默认 direct 模式。"""
        yaml_data = {
            "sub_agents": [
                {
                    "entity_type": "ZDT",
                    "url": "http://localhost:28090/a2a",
                    "name": "zdt_agent",
                }
            ]
        }
        config = SubAgentsConfig.model_validate(yaml_data)
        entry = config.sub_agents[0]
        assert entry.endpoint_type == "direct"
        assert entry.url == "http://localhost:28090/a2a"
