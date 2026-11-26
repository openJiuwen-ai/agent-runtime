# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""高级配置五开关与 data 校验。"""

from __future__ import annotations

import pytest

from manager_server.schemas.advanced_config_fields import (
    DEFAULT_ADVANCED_CONFIG_SWITCHES,
    normalize_advanced_config_switches,
    validate_advanced_config_data,
)
from manager_server.schemas.template_schemas import AdvancedConfigTemplateCreateBody


def test_create_body_defaults_match_code_defaults() -> None:
    body = AdvancedConfigTemplateCreateBody(template_name="默认高级配置")
    for key, expected in DEFAULT_ADVANCED_CONFIG_SWITCHES.items():
        assert getattr(body, key) is expected
    assert body.enabled is True
    assert body.data is None


def test_validate_data_accepts_null_and_object() -> None:
    assert validate_advanced_config_data(None) is None
    assert validate_advanced_config_data({"k": 1}) == {"k": 1}


def test_validate_data_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="JSON object or null"):
        validate_advanced_config_data([1, 2])


def test_normalize_require_all_for_create() -> None:
    with pytest.raises(ValueError, match="missing required field"):
        normalize_advanced_config_switches(
            {"a4p_enabled": True},
            require_all=True,
        )
    out = normalize_advanced_config_switches(
        dict(DEFAULT_ADVANCED_CONFIG_SWITCHES),
        require_all=True,
    )
    assert out == DEFAULT_ADVANCED_CONFIG_SWITCHES


def test_normalize_patch_only_present_keys() -> None:
    out = normalize_advanced_config_switches({"a4p_enabled": True})
    assert out == {"a4p_enabled": True}


def test_normalize_rejects_non_bool() -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        normalize_advanced_config_switches({"a4p_enabled": 1})
