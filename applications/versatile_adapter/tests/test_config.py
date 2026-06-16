# coding: utf-8
"""配置路径解析 + Settings 测试。

按 TECH_VersatileAdapter.md §2.5 验证：
- _resolve_default_config_path 优先级链
- Settings 字段解析（含 alias va_workflow_result_node）
- redis_url 属性构造
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from config import Settings, get_settings
from dispatcher.runner import _resolve_default_config_path, _DEPLOY_DEFAULT_CONFIG_PATH, _LOCAL_DEFAULT_CONFIG_PATH


class TestResolveConfigPath:
    @staticmethod
    def test_env_variable_highest_priority(tmp_path, monkeypatch):
        custom = tmp_path / "custom.yaml"
        monkeypatch.setenv("VERSATILE_PROXY_CONFIG_PATH", str(custom))
        assert _resolve_default_config_path() == custom

    @staticmethod
    def test_deploy_path_when_exists(monkeypatch, tmp_path):
        monkeypatch.delenv("VERSATILE_PROXY_CONFIG_PATH", raising=False)
        deploy_path = tmp_path / "deploy.yaml"
        deploy_path.write_text("test", encoding="utf-8")
        with patch("dispatcher.runner._DEPLOY_DEFAULT_CONFIG_PATH", deploy_path):
            assert _resolve_default_config_path() == deploy_path

    @staticmethod
    def test_local_default_when_no_env_no_deploy(monkeypatch):
        monkeypatch.delenv("VERSATILE_PROXY_CONFIG_PATH", raising=False)
        with patch("dispatcher.runner._DEPLOY_DEFAULT_CONFIG_PATH", Path("/nonexistent")):
            path = _resolve_default_config_path()
        assert path == _LOCAL_DEFAULT_CONFIG_PATH


class TestSettings:
    @staticmethod
    def test_redis_url_without_password(monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        s = Settings(redis_host="10.0.0.1", redis_port=6379, redis_db=0)
        assert s.redis_url == "redis://10.0.0.1:6379/0"

    @staticmethod
    def test_redis_url_with_password(monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        s = Settings(redis_host="10.0.0.1", redis_port=6379, redis_db=1, redis_password="secret")
        assert "secret" in s.redis_url or "%3A" in s.redis_url  # URL-encoded password
        assert s.redis_url.startswith("redis://:")

    @staticmethod
    def test_va_workflow_result_node_alias(monkeypatch):
        """环境变量 VA_WORKFLOW_RESULT_NODE 映射到 versatile_workflow_result_node。"""
        monkeypatch.setenv("VA_WORKFLOW_RESULT_NODE", "TestNode")
        s = Settings()
        assert s.versatile_workflow_result_node == "TestNode"

    @staticmethod
    def test_default_headers_template(monkeypatch):
        monkeypatch.delenv("VERSATILE_HEADERS_TEMPLATE", raising=False)
        s = Settings()
        assert s.versatile_headers_template.get("Accept") is not None
        assert s.versatile_headers_template.get("stream") == "true"

    @staticmethod
    def test_redis_fields_default_none(monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        s = Settings()
        assert s.redis_host is None
        assert s.redis_port is None
        assert s.redis_db is None
        assert s.redis_password is None
