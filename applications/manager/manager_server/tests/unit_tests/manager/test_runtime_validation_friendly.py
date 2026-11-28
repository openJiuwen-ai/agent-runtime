# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""预检失败 → 可本地化 TemplateValidationError（友好报错）。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from manager_server.core.instance_resource import runtime_config_sync as rcs
from manager_server.core.template.errors import TemplateValidationError
from manager_server.core.template.runtime_validation import (
    classify_message,
    raise_from_validation_errors,
    validation_http_detail,
)
from manager_server.routers.template_routers import _http_from_value_error


def _template_row(**overrides) -> dict:
    row = {
        "template_id": "tpl-1",
        "main_container_id": "c-1",
        "sidecar_container_ids": None,
        "volumes": [{"name": "data", "hostPath": {"path": "/tmp/data"}}],
        "enabled": True,
        "data": {},
    }
    row.update(overrides)
    return row


def _container_wire(**overrides) -> dict:
    row = {
        "container_id": "c-1",
        "name": "agent",
        "image": "agentserver:1.0",
        "ports": [{"name": "sse", "containerPort": 8766}],
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        "env": [{"name": "A", "value": "1"}],
    }
    row.update(overrides)
    return row


def test_classify_duplicate_env_message() -> None:
    err = classify_message(
        "containers[0].env[1].name 'QA_V_RUN' duplicates an earlier env entry"
    )
    assert isinstance(err, TemplateValidationError)
    assert err.code == "duplicate_env_name"
    assert err.fields["name"] == "QA_V_RUN"
    assert "QA_V_RUN" in str(err)
    assert "重复" in str(err)


def test_classify_volumes_not_mounted() -> None:
    err = classify_message(
        "template 'tpl-1' volumes not mounted by any container: ['data1', 'data2']"
    )
    assert isinstance(err, TemplateValidationError)
    assert err.code == "volumes_not_mounted"
    assert err.fields["names"] == ["data1", "data2"]


def test_raise_from_validation_errors_drops_cascade_noise() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        raise_from_validation_errors(
            [
                {
                    "path": "containers[0]",
                    "message": (
                        "containers[0].env[1].name 'QA_V_RUN' "
                        "duplicates an earlier env entry"
                    ),
                },
                {
                    "path": "containers[c-1]",
                    "message": "referenced container not present in the containers section",
                },
                {
                    "path": "templates[tpl-1]",
                    "message": (
                        "template 'tpl-1' main_container_id 'c-1' "
                        "not present in the containers section"
                    ),
                },
            ],
            template_id="tpl-1",
        )
    assert exc.value.code == "duplicate_env_name"
    assert "not present" not in str(exc.value)


def test_preflight_duplicate_env_raises_structured_error() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        rcs.preflight_template_binding(
            _template_row(),
            [
                _container_wire(
                    env=[
                        {"name": "QA_V_RUN", "value": "NEW"},
                        {"name": "QA_V_RUN", "value": "OLD"},
                    ]
                )
            ],
        )
    assert exc.value.code == "duplicate_env_name"
    assert exc.value.fields["name"] == "QA_V_RUN"


def test_preflight_undefined_volume_raises_structured_error() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        rcs.preflight_template_binding(
            _template_row(volumes=None),
            [_container_wire()],
        )
    assert exc.value.code == "volume_mount_undefined"
    assert exc.value.fields["name"] == "data"


def test_preflight_unused_volume_raises_structured_error() -> None:
    with pytest.raises(TemplateValidationError) as exc:
        rcs.preflight_template_binding(
            _template_row(
                volumes=[
                    {"name": "data", "hostPath": {"path": "/tmp/data"}},
                    {"name": "data1", "emptyDir": {}},
                ]
            ),
            [_container_wire()],
        )
    assert exc.value.code == "volumes_not_mounted"
    assert "data1" in exc.value.fields["names"]


def test_http_from_value_error_exposes_code_for_frontend() -> None:
    err = TemplateValidationError("duplicate_env_name", "x", name="QA_V_RUN")
    http_exc = _http_from_value_error(err)
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == 400
    assert http_exc.detail == validation_http_detail(err)
    assert http_exc.detail["code"] == "duplicate_env_name"
    assert http_exc.detail["name"] == "QA_V_RUN"


def test_http_from_value_error_classifies_runtime_english_string() -> None:
    http_exc = _http_from_value_error(
        ValueError(
            "containers[0].env[1].name 'QA_V_RUN' duplicates an earlier env entry"
        )
    )
    assert http_exc.detail["code"] == "duplicate_env_name"
