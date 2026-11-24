"""容器模板 Create/Update：镜像字段必填校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manager_server.schemas.template_schemas import (
    ServiceConfigContainerCreateBody,
    ServiceConfigContainerUpdateBody,
)


def _create_kwargs(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "template_name": "agent-main",
        "container_id": "c-agent",
        "name": "agent",
        "image": "registry.example.com/agent:latest",
    }
    body.update(overrides)
    return body


def test_create_requires_non_empty_image() -> None:
    body = ServiceConfigContainerCreateBody(**_create_kwargs())
    assert body.image == "registry.example.com/agent:latest"

    with pytest.raises(ValidationError):
        ServiceConfigContainerCreateBody(**_create_kwargs(image=""))

    with pytest.raises(ValidationError):
        ServiceConfigContainerCreateBody(**_create_kwargs(image="   "))


def test_create_rejects_missing_image() -> None:
    payload = _create_kwargs()
    del payload["image"]
    with pytest.raises(ValidationError):
        ServiceConfigContainerCreateBody(**payload)


def test_update_rejects_blank_image() -> None:
    # 未传 image：允许（部分更新）
    ok = ServiceConfigContainerUpdateBody(template_name="renamed")
    assert ok.image is None

    with pytest.raises(ValidationError):
        ServiceConfigContainerUpdateBody(image="")

    with pytest.raises(ValidationError):
        ServiceConfigContainerUpdateBody(image="   ")
