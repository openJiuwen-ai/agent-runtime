# coding: utf-8
"""Manager 保存前预检（issue #203）：模板 + 容器组合走 foundation 共用校验。"""

from __future__ import annotations

import pytest

from manager_server.core.instance_resource import runtime_config_sync as rcs


def _template_row(**overrides) -> dict:
    row = {
        "template_id": "tpl-1",
        "main_container_id": "c-1",
        "sidecar_container_ids": None,
        "volumes": None,
        "enabled": True,
        "data": {},
    }
    row.update(overrides)
    return row


def _container_wire() -> dict:
    return {
        "container_id": "c-1",
        "name": "agent",
        "image": "agentserver:1.0",
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
    }


def test_preflight_template_binding_raises_with_issue_detail():
    """容器挂载了模板未定义的卷 → 本地预检直接拒绝（issue #203 回归）。"""
    from manager_server.core.template.errors import TemplateValidationError

    with pytest.raises(TemplateValidationError) as exc:
        rcs.preflight_template_binding(_template_row(), [_container_wire()])
    assert exc.value.code == "volume_mount_undefined"
    assert exc.value.fields.get("name") == "data"
    assert "data" in str(exc.value)


def test_preflight_template_binding_accepts_valid_combination():
    row = _template_row(
        volumes=[{"name": "data", "hostPath": {"path": "/tmp/data"}}])
    rcs.preflight_template_binding(row, [_container_wire()])


def test_preflight_skips_disabled_template():
    """已停用模板不在下发投影里，不参与预检。"""
    rcs.preflight_template_binding(
        _template_row(enabled=False), [_container_wire()])


def test_validate_runtime_config_payload_aggregates_errors():
    """本地校验聚合返回错误（与 Runtime config_sync 同一套规则）。"""
    payload = {
        "containers": [_container_wire()],
        "templates": [{
            "template_id": "tpl-1",
            "main_container_id": "c-1",
        }],
        "scopes": [],
    }
    errors = rcs.validate_runtime_config_payload(payload)
    assert errors
    text = rcs.format_validation_errors(errors)
    assert "not defined in the template volumes" in text
