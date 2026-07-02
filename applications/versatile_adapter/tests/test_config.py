# coding: utf-8
"""配置路径解析 + Settings 测试。

按 TECH_VersatileAdapter.md §2.5 验证：
- Settings 字段解析（含 alias va_workflow_result_node）
- redis_url 属性构造
"""
from __future__ import annotations

from config import Settings


def _clear_redis_env(monkeypatch):
    for name in ("REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD", "REDIS_SESSION_TTL"):
        monkeypatch.delenv(name, raising=False)


class TestSettings:
    @staticmethod
    def test_redis_url_without_password(monkeypatch):
        _clear_redis_env(monkeypatch)
        s = Settings(_env_file=None, redis_host="10.0.0.1", redis_port=6379, redis_db=0)
        assert s.redis_url == "redis://10.0.0.1:6379/0"

    @staticmethod
    def test_redis_url_with_password(monkeypatch):
        _clear_redis_env(monkeypatch)
        s = Settings(_env_file=None, redis_host="10.0.0.1", redis_port=6379, redis_db=1, redis_password="secret")
        assert "secret" in s.redis_url or "%3A" in s.redis_url  # URL-encoded password
        assert s.redis_url.startswith("redis://:")

    @staticmethod
    def test_va_workflow_result_node_alias(monkeypatch):
        """环境变量 VA_WORKFLOW_RESULT_NODE 映射到 versatile_workflow_result_node。"""
        monkeypatch.setenv("VA_WORKFLOW_RESULT_NODE", "TestNode")
        s = Settings(_env_file=None)
        assert s.versatile_workflow_result_node == "TestNode"

    @staticmethod
    def test_default_headers_template(monkeypatch):
        monkeypatch.delenv("VERSATILE_HEADERS_TEMPLATE", raising=False)
        s = Settings(_env_file=None)
        assert s.versatile_headers_template.get("Accept") is not None
        assert s.versatile_headers_template.get("stream") == "true"

    @staticmethod
    def test_redis_fields_default_none(monkeypatch):
        _clear_redis_env(monkeypatch)
        s = Settings(_env_file=None)
        assert s.redis_host is None
        assert s.redis_port is None
        assert s.redis_db is None
        assert s.redis_password is None
