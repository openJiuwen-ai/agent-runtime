# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""VA 配置层单元测试。

锁定 ``versatile_headers_template`` 字段的两条契约：

  1. 默认值不含 ``Cookie``：避免把环境特定的 Session token（如 ``AGENT_SID=testUser|0``）
     钉死在代码里。换测试环境时 Cookie 不需要的工作流不会被错误注入；需要的
     环境通过 ``VERSATILE_HEADERS_TEMPLATE`` 环境变量显式注入。
  2. 环境变量 ``VERSATILE_HEADERS_TEMPLATE`` 能覆盖默认值，注入自定义 Cookie。

历史（c1a46ad）曾把 ``AGENT_SID=testUser|0`` 硬编码进默认值，跨环境共用同一份代码
时会触发 VA "URL.project_id ↔ token.project_id mismatch" 的 403。改回纯通用头
（Accept/stream），把环境特定值收回到 .env / 部署配置。
"""
from __future__ import annotations

import importlib

import pytest


def _load_fresh_settings_class():
    """重新 import config 模块，绕开 ``get_settings`` 的 ``@lru_cache``。

    Settings 在 import 时把 env_file 路径绑死；在 monkeypatch 设置环境变量后再
    重 import 才会让新的环境变量被读到。
    """
    import config as _cfg
    return importlib.reload(_cfg)


def test_default_headers_template_has_no_cookie(monkeypatch):
    """默认值不含 Cookie，避免把环境特定 Session token 钉死在代码里。"""
    # 显式清掉可能由 shell 环境引入的覆盖
    monkeypatch.delenv("VERSATILE_HEADERS_TEMPLATE", raising=False)

    cfg = _load_fresh_settings_class()
    settings = cfg.Settings()

    headers = settings.versatile_headers_template
    assert isinstance(headers, dict)
    assert "Cookie" not in headers, (
        f"默认 headers 不应注入 Cookie，但拿到 {headers!r}；"
        f"环境特定 token 必须由 VERSATILE_HEADERS_TEMPLATE env 显式提供"
    )
    # 通用的非环境特定头仍然保留
    assert headers.get("Accept") == "application/json, text/event-stream"
    assert headers.get("stream") == "true"


def test_env_override_can_inject_cookie(monkeypatch):
    """env 设置 VERSATILE_HEADERS_TEMPLATE 时能注入 Cookie，覆盖默认值。"""
    monkeypatch.setenv(
        "VERSATILE_HEADERS_TEMPLATE",
        '{"Cookie":"AGENT_SID=realUser|7",'
        '"Accept":"application/json, text/event-stream",'
        '"stream":"true"}',
    )

    cfg = _load_fresh_settings_class()
    settings = cfg.Settings()

    headers = settings.versatile_headers_template
    assert headers.get("Cookie") == "AGENT_SID=realUser|7"
    assert headers.get("Accept") == "application/json, text/event-stream"
    assert headers.get("stream") == "true"


def test_env_example_does_not_have_empty_assignment():
    """回归测试：``.env.example`` 不能含 ``VERSATILE_HEADERS_TEMPLATE=`` 空赋值。

    pydantic Json 字段 parse 空字符串会抛 ValidationError 导致 VA 启动失败。
    若要在 .env.example 里展示该字段，必须以 ``# `` 注释开头（示范用途），
    或者完全不出现这一行（未设 env → 走代码默认）。

    历史教训：4891ac0 提交曾留下 ``VERSATILE_HEADERS_TEMPLATE=`` 空赋值，复制
    .env.example → .env 就会让 VA 启动失败；本测试防止再次踩坑。
    """
    from pathlib import Path

    env_example = Path(__file__).parent.parent / ".env.example"
    assert env_example.exists(), f".env.example 缺失：{env_example}"
    for lineno, raw in enumerate(env_example.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.lstrip()
        # 注释行允许出现
        if line.startswith("#"):
            continue
        if line.startswith("VERSATILE_HEADERS_TEMPLATE="):
            value = line.split("=", 1)[1].strip()
            assert value, (
                f".env.example 第 {lineno} 行存在空赋值 ``VERSATILE_HEADERS_TEMPLATE=``，"
                f"会让 pydantic Json 字段 parse 失败、VA 启动失败。请删除该行或改为注释（# 开头）"
            )
