"""INT32 列用户输入字段须在有符号 32 位范围内，避免 MySQL 1264。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manager_server.routers.application_config_routers import CronPolicyUpsertRequest
from manager_server.schemas.application_config_schemas import (
    LogMaskingRuleCreateBody,
    LogMaskingRuleUpdateBody,
)
from manager_server.schemas.common_schemas import INT32_MAX, INT32_MIN
from manager_server.schemas.instance_resource_schemas import (
    CreateInstanceServiceResourceBody,
    UpdateInstanceServiceResourceBody,
)
from manager_server.schemas.link_binding_schemas import LinkBindingCreateBody
from manager_server.schemas.quota_schemas import (
    TeamQuotaPolicyUpsertBody,
    WorkspaceQuotaPolicyCreateBody,
    WorkspaceQuotaPolicyPatchBody,
)
from manager_server.schemas.template_schemas import (
    ModelTemplateCreateBody,
    ModelTemplateUpdateBody,
)

_MATCH = "group_id == 'g1'"


def _assert_priority_error(exc: ValidationError) -> None:
    assert any(err["loc"][-1] == "priority" for err in exc.errors())


def test_service_resource_priority_rejects_out_of_range():
    with pytest.raises(ValidationError) as caught:
        CreateInstanceServiceResourceBody(
            ref_template_id="tpl-1",
            match_exprs=[_MATCH],
            resource_name="r",
            priority=10**19,
        )
    _assert_priority_error(caught.value)

    with pytest.raises(ValidationError) as caught:
        UpdateInstanceServiceResourceBody(
            match_exprs=[_MATCH],
            resource_name="r",
            priority=INT32_MAX + 1,
        )
    _assert_priority_error(caught.value)

    ok = CreateInstanceServiceResourceBody(
        ref_template_id="tpl-1",
        match_exprs=[_MATCH],
        resource_name="r",
        priority=INT32_MIN,
    )
    assert ok.priority == INT32_MIN


def test_log_masking_priority_rejects_out_of_range():
    with pytest.raises(ValidationError) as caught:
        LogMaskingRuleCreateBody(rule_name="r", pattern="a", priority=INT32_MAX + 1)
    _assert_priority_error(caught.value)

    with pytest.raises(ValidationError) as caught:
        LogMaskingRuleUpdateBody(priority=INT32_MIN - 1)
    _assert_priority_error(caught.value)


def test_quota_priority_rejects_out_of_range():
    with pytest.raises(ValidationError) as caught:
        WorkspaceQuotaPolicyCreateBody(
            policy_name="p",
            priority=-1,
            limit_bytes=1,
        )
    _assert_priority_error(caught.value)

    with pytest.raises(ValidationError) as caught:
        WorkspaceQuotaPolicyPatchBody(priority=INT32_MAX + 1)
    _assert_priority_error(caught.value)

    with pytest.raises(ValidationError) as caught:
        TeamQuotaPolicyUpsertBody(priority=INT32_MAX + 1)
    _assert_priority_error(caught.value)


def test_model_timeout_and_cron_reject_out_of_range():
    with pytest.raises(ValidationError) as caught:
        ModelTemplateCreateBody(
            template_name="m",
            api_base="https://example.com/v1",
            api_key="k",
            model_id="id",
            model_provider="openai",
            timeout=INT32_MAX + 1,
        )
    assert any(err["loc"][-1] == "timeout" for err in caught.value.errors())

    with pytest.raises(ValidationError) as caught:
        ModelTemplateUpdateBody(timeout=0)
    assert any(err["loc"][-1] == "timeout" for err in caught.value.errors())

    with pytest.raises(ValidationError) as caught:
        CronPolicyUpsertRequest(max_jobs_per_user=INT32_MAX + 1)
    assert any(err["loc"][-1] == "max_jobs_per_user" for err in caught.value.errors())

    assert CronPolicyUpsertRequest(max_jobs_per_user=INT32_MAX).max_jobs_per_user == INT32_MAX


def test_link_binding_epoch_rejects_out_of_range():
    base = dict(
        mtls_binding_id="b1",
        mtls_gateway_endpoint="gw:443",
        mtls_runtime_endpoint="rt:443",
        manager_cert_fingerprint="a" * 64,
        gateway_cert_fingerprint="b" * 64,
        runtime_cert_fingerprint="c" * 64,
        agentserver_cert_fingerprint="d" * 64,
        trust_bundle_ref="ref",
    )
    with pytest.raises(ValidationError) as caught:
        LinkBindingCreateBody(**base, mtls_binding_epoch=INT32_MAX + 1)
    assert any(err["loc"][-1] == "mtls_binding_epoch" for err in caught.value.errors())
