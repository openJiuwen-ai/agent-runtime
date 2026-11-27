# coding: utf-8
"""foundation 配置契约校验（Manager / Runtime 共用，issue #203）。"""

from __future__ import annotations

import pytest

from openjiuwen_runtime.foundation.config_contract.container_spec import (
    parse_container_spec,
)
from openjiuwen_runtime.foundation.config_contract.errors import (
    ConfigContractError,
    set_error_factory,
)
from openjiuwen_runtime.foundation.config_contract.validate import (
    format_validation_errors,
    validate_service_config_payload,
)


def _main_container(**overrides) -> dict:
    container = {
        "container_id": "c-main",
        "name": "agent",
        "image": "agentserver:1.0",
    }
    container.update(overrides)
    return container


def _payload(container: dict, template: dict | None = None) -> dict:
    return {
        "containers": [container],
        "templates": [template or {
            "template_id": "tpl-1",
            "main_container_id": "c-main",
        }],
        "scopes": [],
    }


def test_valid_payload_passes():
    payload = _payload(
        _main_container(volumeMounts=[{"name": "data", "mountPath": "/data"}]),
        {
            "template_id": "tpl-1",
            "main_container_id": "c-main",
            "volumes": [{"name": "data", "hostPath": {"path": "/tmp/data"}}],
        },
    )
    assert validate_service_config_payload(payload) == []


def test_undefined_volume_mount_is_reported_with_template_path():
    """容器 volumeMounts 引用了模板未定义的卷 → 校验失败（issue #203 回归）。"""
    errors = validate_service_config_payload(
        _payload(_main_container(
            volumeMounts=[{"name": "data", "mountPath": "/data"}]))
    )
    text = format_validation_errors(errors)
    assert "not defined in the template volumes" in text
    assert any(item["path"].startswith("templates[") for item in errors)


def test_aggregates_multiple_errors():
    payload = _payload(
        _main_container(env=[{"name": "A", "value": "1"},
                             {"name": "A", "value": "2"}]),
        {"template_id": "tpl-1", "main_container_id": "c-main",
         "scope_concurrency": 0},
    )
    errors = validate_service_config_payload(payload)
    text = format_validation_errors(errors)
    assert "duplicates an earlier env entry" in text
    assert "policy fields invalid" in text
    assert len(errors) >= 2


def test_legacy_inline_template_keys_are_rejected():
    errors = validate_service_config_payload(
        _payload(_main_container(), {
            "template_id": "tpl-1",
            "main_container_id": "c-main",
            "agent_image": "legacy:1",
        })
    )
    assert any("mixes container references" in item["message"] for item in errors)


def test_error_factory_injection_switches_exception_type():
    class _Boom(Exception):
        pass

    set_error_factory(_Boom)
    try:
        with pytest.raises(_Boom):
            parse_container_spec({}, "containers[0]", role="main")
    finally:
        set_error_factory(ConfigContractError)
