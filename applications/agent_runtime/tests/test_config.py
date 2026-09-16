# coding: utf-8
"""AgentRuntimeConfig.from_env 的 default_namespace 解析链:
POD_NAMESPACE(downward API 注入的自身 ns)> "default"。模板 namespace 为
空时 AgentServer 落 default_namespace(k8s 层 falsy 兜底),见
resource_manager/k8s.py。
"""

from __future__ import annotations

from agent_runtime.config import AgentRuntimeConfig


def test_default_namespace_pod_namespace_when_injected(monkeypatch):
    monkeypatch.delenv("POD_NAMESPACE", raising=False)
    monkeypatch.setenv("POD_NAMESPACE", "runtime-own-ns")
    assert AgentRuntimeConfig.from_env().default_namespace == "runtime-own-ns"


def test_default_namespace_empty_pod_namespace_keeps_descending(monkeypatch):
    """env 设了空串 = 未设置,继续下探(or 链归一,防误配落死值)。"""
    monkeypatch.setenv("POD_NAMESPACE", "")
    assert AgentRuntimeConfig.from_env().default_namespace == "default"


def test_default_namespace_literal_default(monkeypatch):
    monkeypatch.delenv("POD_NAMESPACE", raising=False)
    assert AgentRuntimeConfig.from_env().default_namespace == "default"
