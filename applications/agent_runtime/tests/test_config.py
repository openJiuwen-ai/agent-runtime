# coding: utf-8
"""AgentRuntimeConfig 的 default_namespace 解析链(config.own_namespace):
POD_NAMESPACE env(显式覆盖/downward API)> in-cluster SA namespace 文件
(Pod 必挂,零部署配置)> "default"。模板 namespace 为空串时 AgentServer
落 default_namespace(k8s 层 falsy 兜底),见 resource_manager/k8s.py。
"""

from __future__ import annotations

import pytest

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


# ---- health_probe_timeout(AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT,2026-10)----


def test_health_probe_timeout_default(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT", raising=False)
    assert AgentRuntimeConfig.from_env().health_probe_timeout == 3.0


def test_health_probe_timeout_env_overrides(monkeypatch):
    """AgentServer 慢启动/高负载被误杀半死时的调大入口。"""
    monkeypatch.setenv("AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT", "8.5")
    assert AgentRuntimeConfig.from_env().health_probe_timeout == 8.5


def test_health_probe_timeout_invalid_falls_back(monkeypatch):
    """env 手误(非 float)留痕回退默认——与 _env_int 同纪律,配错可见。"""
    monkeypatch.setenv("AGENT_RUNTIME_HEALTH_PROBE_TIMEOUT", "3s")
    assert AgentRuntimeConfig.from_env().health_probe_timeout == 3.0


# ---- deploy_mode(AGENT_RUNTIME_DEPLOY_MODE=full|routing,2026-10 routing 供给模式)----


def test_deploy_mode_default_full(monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_DEPLOY_MODE", raising=False)
    assert AgentRuntimeConfig.from_env().deploy_mode == "full"


def test_deploy_mode_explicit_values(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_DEPLOY_MODE", "routing")
    assert AgentRuntimeConfig.from_env().deploy_mode == "routing"
    monkeypatch.setenv("AGENT_RUNTIME_DEPLOY_MODE", "FULL")
    assert AgentRuntimeConfig.from_env().deploy_mode == "full"  # 大小写归一


def test_deploy_mode_invalid_raises(monkeypatch):
    """模式类开关严格解析:配错启动即 raise,不做静默回退——否则受限权限
    环境误配成 full 会以托管模式启动、首个 deploy/delete 才撞 403。"""
    monkeypatch.setenv("AGENT_RUNTIME_DEPLOY_MODE", "external")
    with pytest.raises(ValueError, match="full\\|routing"):
        AgentRuntimeConfig.from_env()
