# coding: utf-8
"""logsetup 单元测试：Filter/Formatter 请求尾巴、redact 脱敏、env 解析告警。

不触碰全局 root handler（configure_logging 仅进程入口调用），Filter/Formatter
直接以普通对象驱动。
"""

from __future__ import annotations

import logging

from agent_runtime.debug_api import redact
from agent_runtime.logsetup import (
    _ContextFormatter,
    _RequestContextFilter,
    current_log_tail,
    reset_request_log_context,
    set_request_log_context,
)


def _format_with_context(msg: str = "hello") -> str:
    """构造 record → Filter → Formatter 的完整管线输出。"""
    formatter = _ContextFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
    record = logging.LogRecord(
        name="agent_runtime.test", level=logging.INFO, pathname="t.py",
        lineno=1, msg=msg, args=(), exc_info=None, func="f",
    )
    _RequestContextFilter().filter(record)
    return formatter.format(record)


def test_request_tail_appended_and_cleared():
    token = set_request_log_context(request_id="r-1", session_id="s-1",
                                    endpoint="route", instance_id="i-1")
    try:
        line = _format_with_context()
        assert line.endswith(
            "| request_id=r-1 session_id=s-1 endpoint=route instance_id=i-1")
    finally:
        reset_request_log_context(token)
    # 离开请求后：后台任务行无尾巴（与原格式一致）
    assert _format_with_context().endswith("hello")
    assert current_log_tail() == ""


def test_empty_fields_dropped_and_default_empty():
    token = set_request_log_context(request_id="r-2", session_id=None,
                                    endpoint="", instance_id="i-2")
    try:
        assert current_log_tail() == "request_id=r-2 instance_id=i-2"
    finally:
        reset_request_log_context(token)


def test_redact_dict_keys_and_urls():
    value = {
        "kubeconfig": "/secret/path",
        "db_password": "p@ss",
        "redis_url": "redis://user:pass@host:6379/2",
        "plain": "ok",
        "nested": {"api_key": "k", "port": 6379},
        "none_val": None,
    }
    out = redact(value)
    assert out["kubeconfig"] == "***"
    assert out["db_password"] == "***"
    assert out["redis_url"] == "redis://***@host:6379/2"
    assert out["plain"] == "ok"
    assert out["nested"]["api_key"] == "***"
    assert out["nested"]["port"] == 6379
    assert out["none_val"] is None


def test_redact_url_direct_and_depth_cap():
    assert redact("mysql://u:p@db:3306/x") == "mysql://***@db:3306/x"
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}
    assert redact(deep, max_depth=6) is not None  # 不崩即可（超深打码）


def test_config_env_parse_warns(monkeypatch, caplog):
    monkeypatch.setenv("AGENT_RUNTIME_SWEEP_INTERVAL", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="agent_runtime.config"):
        from agent_runtime.config import _env_int
        assert _env_int("AGENT_RUNTIME_SWEEP_INTERVAL", 1) == 1
    assert any("AGENT_RUNTIME_SWEEP_INTERVAL" in r.message
               and "not-a-number" in r.message for r in caplog.records)
