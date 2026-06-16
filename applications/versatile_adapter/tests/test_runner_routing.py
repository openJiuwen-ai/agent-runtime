# coding: utf-8
# pylint: disable=protected-access
# 说明：本文件为单元测试，需要访问 VersatileAdapterRunner 的 protected 成员
# （如 _match_workflow 等）。
"""VersatileAdapterRunner 路由匹配测试。

验证 YAML 加载、controller/workflow 选择、target 匹配优先级。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from adapters.versatile_controller import VersatileController
from adapters.versatile_workflow import VersatileWorkflow
from dispatcher.runner import (
    VersatileAdapterRunner,
    _VersatileAdapterConfig,
    _resolve_default_config_path,
)


# ════════════════════════════════════════════════════════════════════
# YAML 加载
# ════════════════════════════════════════════════════════════════════


def test_load_yaml_creates_all_adapters(write_yaml):
    """YAML 中 3 个 adapter 都被加载，名称类型正确。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    names = [a.name for a in runner._adapters]
    assert names == ["default_controller", "wf_knowledge_qa", "wf_wealth"]
    types = [a.type for a in runner._adapters]
    assert types == ["controller", "workflow", "workflow"]


def test_load_yaml_controller_is_resolved(write_yaml):
    """第一个 type=controller 的 adapter 被选为 _controller_cfg。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    assert runner._controller_cfg is not None
    assert runner._controller_cfg.name == "default_controller"


def test_missing_yaml_falls_back_to_settings_controller(tmp_path):
    """YAML 不存在时回退到 _build_from_settings，只生成 default_controller。"""
    missing = tmp_path / "no-such.yaml"
    runner = VersatileAdapterRunner(config_path=missing)
    assert len(runner._adapters) == 1
    assert runner._adapters[0].name == "default_controller"
    assert runner._adapters[0].type == "controller"


def test_header_whitelist_lowered_and_set(write_yaml):
    """forward_header_whitelist 被转为小写 set。"""
    runner = VersatileAdapterRunner(config_path=write_yaml())
    controller = runner._adapters[0]
    assert controller.forward_header_whitelist == {"x-user-id", "cookie"}


# ════════════════════════════════════════════════════════════════════
# 路由匹配
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def runner(write_yaml):
    return VersatileAdapterRunner(config_path=write_yaml())


def test_match_by_workflow_id(runner):
    cfg = runner._match_workflow({"workflow_id": "wf_knowledge_qa"})
    assert cfg is not None
    assert cfg.name == "wf_knowledge_qa"


def test_match_by_intent(runner):
    cfg = runner._match_workflow({"intent": "knowledge_qa"})
    assert cfg is not None
    assert cfg.name == "wf_knowledge_qa"


def test_match_chinese_intent(runner):
    cfg = runner._match_workflow({"intent": "理财推荐"})
    assert cfg is not None
    assert cfg.name == "wf_wealth"


def test_no_match_returns_none_then_controller_fallback(runner):
    cfg = runner._match_workflow({"intent": "unknown_intent"})
    assert cfg is None  # 后续 run_async 会用 _controller_cfg 兜底


def test_workflow_id_takes_priority_over_intent_when_distinct(runner):
    """workflow_id 与 intent 都给但分别匹配不同 adapter：按代码逻辑取首个命中。"""
    # workflow_id 在循环里先于 intent 判定，应命中 wf_knowledge_qa
    cfg = runner._match_workflow({"workflow_id": "wf_knowledge_qa", "intent": "理财推荐"})
    assert cfg is not None
    assert cfg.name == "wf_knowledge_qa"


def test_empty_target_returns_none(runner):
    cfg = runner._match_workflow({})
    assert cfg is None


# ════════════════════════════════════════════════════════════════════
# Adapter 实例创建
# ════════════════════════════════════════════════════════════════════


def test_create_workflow_adapter_instance(runner):
    cfg = runner._match_workflow({"intent": "knowledge_qa"})
    adapter = runner._create_adapter(cfg)
    assert isinstance(adapter, VersatileWorkflow)
    # 内部字段
    assert adapter._workflow_id == "wf_knowledge_qa"
    assert adapter._timeout == 30


def test_create_controller_adapter_instance(runner):
    adapter = runner._create_adapter(runner._controller_cfg)
    assert isinstance(adapter, VersatileController)
    assert adapter._workflow_result_node == "GXZQAResponseNode"


# ════════════════════════════════════════════════════════════════════
# 配置路径解析（优先级）
# ════════════════════════════════════════════════════════════════════


def test_resolve_path_uses_env_first(tmp_path, monkeypatch):
    """VERSATILE_PROXY_CONFIG_PATH 环境变量优先。"""
    target = tmp_path / "custom.yaml"
    monkeypatch.setenv("VERSATILE_PROXY_CONFIG_PATH", str(target))
    assert _resolve_default_config_path() == target


def test_resolve_path_falls_back_to_local_default(monkeypatch):
    """env 未设，部署路径不存在时，回退本地 versatile_proxy.yaml。"""
    monkeypatch.delenv("VERSATILE_PROXY_CONFIG_PATH", raising=False)
    # mock 部署路径不存在
    with patch("dispatcher.runner._DEPLOY_DEFAULT_CONFIG_PATH", Path("/nonexistent/path.yaml")):
        path = _resolve_default_config_path()
    assert path.name == "versatile_proxy.yaml"
    assert "versatile_adapter" in str(path)
