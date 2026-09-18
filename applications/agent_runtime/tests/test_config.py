# coding: utf-8
"""AgentRuntimeConfig 的 default_namespace 解析链(config.own_namespace):
POD_NAMESPACE env(显式覆盖/downward API)> in-cluster SA namespace 文件
(Pod 必挂,零部署配置)> "default"。模板 namespace 为空串时 AgentServer
落 default_namespace(k8s 层 falsy 兜底),见 resource_manager/k8s.py。
"""

from __future__ import annotations

import agent_runtime.config as config_module
from agent_runtime.config import AgentRuntimeConfig


def _clear_ns_env(monkeypatch):
    monkeypatch.delenv("POD_NAMESPACE", raising=False)


def test_default_namespace_pod_namespace_when_injected(monkeypatch):
    _clear_ns_env(monkeypatch)
    monkeypatch.setattr(config_module, "_SA_NS_FILE", "/nonexistent")
    monkeypatch.setenv("POD_NAMESPACE", "runtime-own-ns")
    assert AgentRuntimeConfig.from_env().default_namespace == "runtime-own-ns"


def test_default_namespace_sa_file_fallback(monkeypatch, tmp_path):
    """env 未注入(存量部署无 POD_NAMESPACE)→ SA 文件兜底自身 ns——
    2026-09-17 cyz2 断层实录的回归锚点:镜像新代码 + 旧模板无注入。"""
    _clear_ns_env(monkeypatch)
    sa = tmp_path / "namespace"
    sa.write_text("cyz2\n")
    monkeypatch.setattr(config_module, "_SA_NS_FILE", str(sa))
    assert AgentRuntimeConfig.from_env().default_namespace == "cyz2"


def test_default_namespace_env_overrides_sa_file(monkeypatch, tmp_path):
    _clear_ns_env(monkeypatch)
    sa = tmp_path / "namespace"
    sa.write_text("from-sa-file\n")
    monkeypatch.setattr(config_module, "_SA_NS_FILE", str(sa))
    monkeypatch.setenv("POD_NAMESPACE", "pinned-ns")
    assert AgentRuntimeConfig.from_env().default_namespace == "pinned-ns"


def test_default_namespace_empty_pod_namespace_keeps_descending(
        monkeypatch, tmp_path):
    """env 设了空串 = 未设置,继续下探(or 链归一,防误配落死值)。"""
    monkeypatch.setenv("POD_NAMESPACE", "")
    sa = tmp_path / "namespace"
    sa.write_text("cyz2\n")
    monkeypatch.setattr(config_module, "_SA_NS_FILE", str(sa))
    assert AgentRuntimeConfig.from_env().default_namespace == "cyz2"


def test_default_namespace_literal_default(monkeypatch, tmp_path):
    """非 K8s(env 与 SA 文件皆无)→ 字面 default(本地/测试形态)。"""
    _clear_ns_env(monkeypatch)
    monkeypatch.setattr(config_module, "_SA_NS_FILE",
                        str(tmp_path / "nonexistent"))
    assert AgentRuntimeConfig.from_env().default_namespace == "default"
