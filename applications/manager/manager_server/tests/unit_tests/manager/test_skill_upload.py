# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""企业版 Skill 按包上传审批：字段校验、幂等建单、授权视图与安装结果。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.approval.service import (
    ApprovalOrderExpiredError,
    ApprovalService,
    _json_value,
)
from manager_server.core.skill_upload.approval import validate_package_info
from manager_server.core.skill_upload.errors import (
    SkillUploadConflictError,
    SkillUploadExpiredError,
    SkillUploadForbiddenError,
    SkillUploadNotFoundError,
    SkillUploadValidationError,
)
from manager_server.core.skill_upload.service import SkillUploadService
from manager_server.infrastructure.utils import iso_datetime

UPLOAD_A = "a" * 32
UPLOAD_B = "b" * 32

pytestmark = pytest.mark.unit


def _descriptor(
    *,
    upload_id: str = UPLOAD_A,
    skill_name: str = "demo-skill",
    digest: str = "3" * 64,
    staged_at: datetime | None = None,
    stage_days: int = 7,
) -> dict:
    now = staged_at or datetime.now(timezone.utc)
    expires = now + timedelta(days=stage_days)
    return {
        "schema_version": 1,
        "upload_id": upload_id,
        "skill_name": skill_name,
        "skill_version": "1.0.0",
        "description": "demo",
        "file_count": 1,
        "uncompressed_bytes": 10,
        "package": {
            "filename": "demo.zip",
            "size_bytes": 20,
            "sha256": "sha256:" + "2" * 64,
            "object_key": f"skill-approvals/{'0' * 64}/{upload_id}/demo.zip",
        },
        "digest": f"sha256:{digest}",
        "staged_at": now.isoformat(),
        "stage_expires_at": expires.isoformat(),
    }


async def _submit(
    service: SkillUploadService,
    descriptor: dict,
    *,
    applicant_id: str = "u_app",
    reason: str = "上传技能",
    approver_id: str = "u_admin",
) -> dict:
    with patch.object(
        ApprovalService, "_ensure_valid_approver", AsyncMock(return_value=None)
    ):
        return await service.submit(
            applicant_id=applicant_id,
            jiuwenclaw_id="cluster_a",
            group_id="g1",
            bot_id="bot1",
            reason=reason,
            approver_id=approver_id,
            replace=False,
            package_info=descriptor,
        )


def test_validate_package_info_rejects_expired_stage() -> None:
    descriptor = _descriptor(
        staged_at=datetime.now(timezone.utc) - timedelta(days=8)
    )

    with pytest.raises(SkillUploadValidationError, match="expired"):
        validate_package_info(descriptor)


def test_validate_package_info_accepts_object_key() -> None:
    descriptor = _descriptor()

    validated = validate_package_info(descriptor)

    assert validated["package"]["object_key"] == (
        f"skill-approvals/{'0' * 64}/{UPLOAD_A}/demo.zip"
    )


def test_validate_package_info_rejects_object_key_outside_prefix() -> None:
    descriptor = _descriptor()
    descriptor["package"]["object_key"] = (
        f"downloads/{'0' * 64}/{UPLOAD_A}/demo.zip"
    )

    with pytest.raises(SkillUploadValidationError, match="object_key"):
        validate_package_info(descriptor)


def test_validate_package_info_rejects_object_key_upload_mismatch() -> None:
    descriptor = _descriptor()
    descriptor["package"]["object_key"] = (
        f"skill-approvals/{'0' * 64}/{UPLOAD_B}/demo.zip"
    )

    with pytest.raises(SkillUploadValidationError, match="upload_id"):
        validate_package_info(descriptor)


def test_validate_package_info_rejects_missing_object_key() -> None:
    descriptor = _descriptor()
    descriptor["package"].pop("object_key")

    with pytest.raises(SkillUploadValidationError, match="object_key"):
        validate_package_info(descriptor)


@pytest.mark.asyncio
async def test_submit_is_idempotent_for_same_upload(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)

    first = await _submit(service, descriptor)
    second = await _submit(service, descriptor)

    assert first == {
        "order_num": f"apr_{UPLOAD_A}",
        "status": "pending",
        "reused": False,
        "approver_id": "u_admin",
    }
    assert second["reused"] is True
    assert second["order_num"] == first["order_num"]


@pytest.mark.asyncio
async def test_submit_rejects_upload_owned_by_another_user(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)

    await _submit(service, descriptor)

    with pytest.raises(SkillUploadForbiddenError):
        await _submit(service, descriptor, applicant_id="u_other")


@pytest.mark.asyncio
async def test_submit_reuses_active_request_with_same_name(
    manager_api,
) -> None:
    first_descriptor = _descriptor(upload_id=UPLOAD_A, digest="3" * 64)
    second_descriptor = _descriptor(upload_id=UPLOAD_B, digest="4" * 64)
    service = SkillUploadService(manager_api.handler)

    first = await _submit(service, first_descriptor)
    second = await _submit(service, second_descriptor)

    assert second["reused"] is True
    assert second["order_num"] == first["order_num"]
    assert second["status"] == "pending"


@pytest.mark.asyncio
async def test_submit_reuses_active_request_with_same_package(
    manager_api,
) -> None:
    first_descriptor = _descriptor(upload_id=UPLOAD_A, skill_name="demo-skill")
    renamed_descriptor = _descriptor(upload_id=UPLOAD_B, skill_name="renamed-skill")
    service = SkillUploadService(manager_api.handler)

    first = await _submit(service, first_descriptor)
    renamed = await _submit(service, renamed_descriptor)

    assert renamed["reused"] is True
    assert renamed["order_num"] == first["order_num"]


@pytest.mark.asyncio
async def test_submit_reuses_approved_request_before_install(
    manager_api,
) -> None:
    first_descriptor = _descriptor(upload_id=UPLOAD_A)
    second_descriptor = _descriptor(upload_id=UPLOAD_B)
    service = SkillUploadService(manager_api.handler)

    first = await _submit(service, first_descriptor)
    await manager_api.handler.update(
        "approval_order",
        {"order_num": first["order_num"]},
        {"status": "approved", "result_data": {"install_status": "pending"}},
    )

    second = await _submit(service, second_descriptor)

    assert second["reused"] is True
    assert second["order_num"] == first["order_num"]
    assert second["status"] == "approved"


@pytest.mark.asyncio
async def test_submit_ignores_expired_request(
    manager_api,
) -> None:
    first_descriptor = _descriptor(upload_id=UPLOAD_A)
    service = SkillUploadService(manager_api.handler)
    first = await _submit(service, first_descriptor)

    await manager_api.handler.update(
        "approval_order",
        {"order_num": first["order_num"]},
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
    )

    second_descriptor = _descriptor(upload_id=UPLOAD_B)
    second = await _submit(service, second_descriptor)

    assert second["reused"] is False
    assert second["order_num"] != first["order_num"]


@pytest.mark.asyncio
async def test_submit_same_upload_after_expiry_returns_410(
    manager_api,
) -> None:
    descriptor = _descriptor(upload_id=UPLOAD_A)
    service = SkillUploadService(manager_api.handler)
    first = await _submit(service, descriptor)

    await manager_api.handler.update(
        "approval_order",
        {"order_num": first["order_num"]},
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
    )

    with pytest.raises(SkillUploadExpiredError):
        await _submit(service, descriptor)


@pytest.mark.asyncio
async def test_submit_ignores_installed_request(
    manager_api,
) -> None:
    first_descriptor = _descriptor(upload_id=UPLOAD_A)
    service = SkillUploadService(manager_api.handler)
    first = await _submit(service, first_descriptor)
    await manager_api.handler.update(
        "approval_order",
        {"order_num": first["order_num"]},
        {"status": "approved", "result_data": {"install_status": "installed"}},
    )

    second_descriptor = _descriptor(upload_id=UPLOAD_B, digest="4" * 64)
    second = await _submit(service, second_descriptor)

    assert second["reused"] is False
    assert second["order_num"] != first["order_num"]


@pytest.mark.asyncio
async def test_find_active_matches_same_name_or_package(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]

    by_name = await service.find_active(
        applicant_id="u_app",
        cluster_id="cluster_a",
        skill_name="demo-skill",
        digest="sha256:" + "4" * 64,
    )
    by_package = await service.find_active(
        applicant_id="u_app",
        cluster_id="cluster_a",
        skill_name="another-skill",
        digest=descriptor["digest"],
    )
    unrelated = await service.find_active(
        applicant_id="u_app",
        cluster_id="cluster_a",
        skill_name="another-skill",
        digest="sha256:" + "4" * 64,
    )
    other_user = await service.find_active(
        applicant_id="u_other",
        cluster_id="cluster_a",
        skill_name="demo-skill",
        digest=descriptor["digest"],
    )

    assert by_name is not None and by_name["order_num"] == order_num
    assert by_package is not None and by_package["order_num"] == order_num
    assert unrelated is None
    assert other_user is None


@pytest.mark.asyncio
async def test_find_active_skips_installed_and_expired(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]

    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {"status": "approved", "result_data": {"install_status": "installed"}},
    )
    assert (
        await service.find_active(
            applicant_id="u_app",
            cluster_id="cluster_a",
            skill_name="demo-skill",
            digest=descriptor["digest"],
        )
        is None
    )

    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {
            "status": "pending",
            "result_data": {},
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        },
    )
    assert (
        await service.find_active(
            applicant_id="u_app",
            cluster_id="cluster_a",
            skill_name="demo-skill",
            digest=descriptor["digest"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_find_active_survives_more_than_limit_installed_history(
    manager_api,
) -> None:
    """历史已安装单超过 limit 时，仍能命中最新创建的进行中单。"""
    handler = manager_api.handler
    now = datetime.now(timezone.utc)
    for index in range(100):
        await handler.create(
            "approval_order",
            {
                "cluster_id": "cluster_a",
                "order_num": f"apr_history_{index:03d}",
                "title": "上传技能 history-skill",
                "applicant_id": "u_app",
                "bot_id": "bot1",
                "status": "approved",
                "business_type": "skill_upload",
                "reason": "上传技能",
                "apply_data": {},
                "result_data": {"install_status": "installed"},
                "created_at": now,
                "updated_at": now,
            },
        )

    descriptor = _descriptor()
    service = SkillUploadService(handler)
    order_num = (await _submit(service, descriptor))["order_num"]

    active = await service.find_active(
        applicant_id="u_app",
        cluster_id="cluster_a",
        skill_name="demo-skill",
        digest=descriptor["digest"],
    )

    assert active is not None and active["order_num"] == order_num


@pytest.mark.asyncio
async def test_authorization_view_requires_same_cluster(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    submitted = await _submit(service, descriptor)
    order_num = submitted["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": order_num}, {"status": "approved"}
    )

    view = await service.get_order(
        order_num=order_num,
        viewer_id="u_app",
        view="authorization",
        jiuwenclaw_id="cluster_a",
    )

    assert view["upload_id"] == UPLOAD_A
    assert view["digest"] == descriptor["digest"]
    assert view["status"] == "approved"

    with pytest.raises(SkillUploadForbiddenError):
        await service.get_order(
            order_num=order_num,
            viewer_id="u_app",
            view="authorization",
            jiuwenclaw_id="cluster_b",
        )


@pytest.mark.asyncio
async def test_install_result_is_recorded_once(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": order_num}, {"status": "approved"}
    )

    first = await service.record_install_result(
        order_num=order_num,
        viewer_id="u_app",
        jiuwenclaw_id="cluster_a",
        upload_id=UPLOAD_A,
        digest=descriptor["digest"],
        status="installed",
        detail=None,
    )
    second = await service.record_install_result(
        order_num=order_num,
        viewer_id="u_app",
        jiuwenclaw_id="cluster_a",
        upload_id=UPLOAD_A,
        digest=descriptor["digest"],
        status="installed",
        detail=None,
    )

    assert first["install_status"] == "installed"
    assert second["install_status"] == "installed"
    order = await manager_api.handler.get("approval_order", {"order_num": order_num})
    result_data = _json_value(getattr(order, "result_data", None), {})
    assert isinstance(result_data, dict)
    assert result_data["install_status"] == "installed"


@pytest.mark.asyncio
async def test_install_result_rejects_digest_mismatch(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": order_num}, {"status": "approved"}
    )

    with pytest.raises(SkillUploadConflictError):
        await service.record_install_result(
            order_num=order_num,
            viewer_id="u_app",
            jiuwenclaw_id="cluster_a",
            upload_id=UPLOAD_A,
            digest="sha256:" + "9" * 64,
            status="installed",
            detail=None,
        )


def _package_order(*, content: bytes, expires_in_days: int = 1) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    return {
        "business_type": "skill_upload",
        "expires_at": expires,
        "apply_data": {
            "package": {
                "filename": "demo.zip",
                "object_key": (
                    f"skill-approvals/{'0' * 64}/{UPLOAD_A}/demo.zip"
                ),
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
        },
    }


@pytest.mark.asyncio
async def test_read_package_reads_declared_object_key(manager_api) -> None:
    service = SkillUploadService(manager_api.handler)
    content = b"zip-payload"
    order = _package_order(content=content)
    fetch = AsyncMock(return_value=content)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=order)
    ), patch("manager_server.core.skill_upload.service.fetch_package", fetch):
        filename, data = await service.read_package(order_num="apr_x")

    assert filename == "demo.zip"
    assert data == content
    fetch.assert_awaited_once_with(
        order["apply_data"]["package"]["object_key"],
        order["apply_data"]["package"]["sha256"],
    )


@pytest.mark.asyncio
async def test_read_package_rejects_expired_order(manager_api) -> None:
    service = SkillUploadService(manager_api.handler)
    order = _package_order(content=b"zip-payload", expires_in_days=-1)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=order)
    ), patch(
        "manager_server.core.skill_upload.service.fetch_package",
        AsyncMock(return_value=b"zip-payload"),
    ) as fetch:
        with pytest.raises(SkillUploadExpiredError):
            await service.read_package(order_num="apr_x")

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_package_rejects_unknown_order(manager_api) -> None:
    service = SkillUploadService(manager_api.handler)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=None)
    ):
        with pytest.raises(SkillUploadNotFoundError):
            await service.read_package(order_num="apr_missing")


@pytest.mark.asyncio
async def test_list_mine_exposes_package_expiry(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    await _submit(service, descriptor)

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status=None,
        page=1,
        page_size=20,
    )

    assert listing["items"][0]["package_expires_at"] == iso_datetime(
        (await manager_api.handler.get("approval_order", {"order_num": f"apr_{UPLOAD_A}"})).expires_at
    )


@pytest.mark.asyncio
async def test_list_mine_exposes_install_failure_detail(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": order_num}, {"status": "approved"}
    )
    await service.record_install_result(
        order_num=order_num,
        viewer_id="u_app",
        jiuwenclaw_id="cluster_a",
        upload_id=UPLOAD_A,
        digest=descriptor["digest"],
        status="failed",
        detail="staged package is expired",
    )

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="active",
        status=None,
        page=1,
        page_size=20,
    )

    item = listing["items"][0]
    assert item["install_status"] == "failed"
    assert item["install_detail"] == "staged package is expired"


@pytest.mark.asyncio
async def test_act_on_expired_order_marks_expired_without_record(
    manager_api,
) -> None:
    descriptor = _descriptor()
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, descriptor))["order_num"]
    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
    )

    with pytest.raises(ApprovalOrderExpiredError):
        await ApprovalService(manager_api.handler).act(
            order_num=order_num,
            operator_id="u_admin",
            action="approve",
            comment=None,
        )

    order = await manager_api.handler.get("approval_order", {"order_num": order_num})
    assert order.status == "expired"
    records = await manager_api.handler.list_records(
        "approval_record", {"order_num": order_num}, limit=100, offset=0
    )
    assert records == []


@pytest.mark.asyncio
async def test_expire_overdue_orders_only_touches_overdue_pending(
    manager_api,
) -> None:
    service = SkillUploadService(manager_api.handler)
    overdue = (await _submit(service, _descriptor(upload_id=UPLOAD_A)))["order_num"]
    await manager_api.handler.update(
        "approval_order",
        {"order_num": overdue},
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
    )
    active = (await _submit(service, _descriptor(upload_id=UPLOAD_B)))["order_num"]

    expired = await ApprovalService(manager_api.handler).expire_overdue_orders()

    assert expired == 1
    assert (
        await manager_api.handler.get("approval_order", {"order_num": overdue})
    ).status == "expired"
    assert (
        await manager_api.handler.get("approval_order", {"order_num": active})
    ).status == "pending"


@pytest.mark.asyncio
async def test_list_mine_marks_overdue_order_expired(manager_api) -> None:
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, _descriptor()))["order_num"]
    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
    )

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status=None,
        page=1,
        page_size=20,
    )

    assert listing["items"][0]["status"] == "expired"


@pytest.mark.asyncio
async def test_upload_request_status_filter_accepts_expired_only(manager_api) -> None:
    """`status` 契约：expired 合法；cancelled 与未知值在路由层被拒绝。"""
    service = SkillUploadService(manager_api.handler)
    order_num = (
        await _submit(service, _descriptor(), applicant_id="admin")
    )["order_num"]
    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {"status": "expired"},
    )

    expired = await manager_api.http.get(
        "/api/v1/user-console/skills/upload-requests",
        params={"scope": "all", "status": "expired"},
    )
    assert expired.status_code == 200, expired.text
    assert [item["order_num"] for item in expired.json()["data"]["items"]] == [
        order_num
    ]

    mine = await manager_api.http.get(
        "/api/v1/user-console/approvals/mine",
        params={"status": "expired"},
    )
    assert mine.status_code == 200, mine.text

    for invalid in ("cancelled", "bogus"):
        rejected = await manager_api.http.get(
            "/api/v1/user-console/skills/upload-requests",
            params={"status": invalid},
        )
        assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
async def test_list_mine_pages_with_has_next(manager_api) -> None:
    """分页只取到当前页之后一条，用 has_next 表示是否还有下一页。"""
    service = SkillUploadService(manager_api.handler)
    for index in range(3):
        await _submit(
            service,
            _descriptor(
                upload_id=f"{index:032x}",
                skill_name=f"demo-{index}",
                digest=f"{index + 1:064x}",
            ),
        )

    first = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status=None,
        page=1,
        page_size=2,
    )
    assert len(first["items"]) == 2
    assert first["has_next"] is True
    assert "total" not in first

    second = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status=None,
        page=2,
        page_size=2,
    )
    assert len(second["items"]) == 1
    assert second["has_next"] is False


@pytest.mark.asyncio
async def test_list_mine_scans_past_scope_filtered_rows(manager_api) -> None:
    """scope 在内存过滤：历史单排在大量 active 单之后也要能取到。"""
    service = SkillUploadService(manager_api.handler)
    history_num = (
        await _submit(
            service,
            _descriptor(upload_id=f"{100:032x}", skill_name="history-skill"),
        )
    )["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": history_num}, {"status": "rejected"}
    )
    for index in range(3):
        await _submit(
            service,
            _descriptor(
                upload_id=f"{index:032x}",
                skill_name=f"active-{index}",
                digest=f"{index + 2:064x}",
            ),
        )

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="history",
        status=None,
        page=1,
        page_size=2,
    )

    assert [item["order_num"] for item in listing["items"]] == [history_num]
    assert listing["has_next"] is False


@pytest.mark.asyncio
async def test_list_mine_status_expired_catches_just_expired(manager_api) -> None:
    """status 在懒刷新之后过滤：库里仍是 pending、刚过期的单也要命中 expired。"""
    service = SkillUploadService(manager_api.handler)
    order_num = (await _submit(service, _descriptor()))["order_num"]
    await manager_api.handler.update(
        "approval_order",
        {"order_num": order_num},
        {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)},
    )

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status="expired",
        page=1,
        page_size=20,
    )

    assert [item["order_num"] for item in listing["items"]] == [order_num]
    assert listing["has_next"] is False


@pytest.mark.asyncio
async def test_list_mine_scope_splits_active_and_history(manager_api) -> None:
    """scope 语义：active 只含待审批/待安装；history 含驳回、过期与已安装。"""
    service = SkillUploadService(manager_api.handler)
    pending_num = (
        await _submit(
            service,
            _descriptor(upload_id=f"{1:032x}", skill_name="pending-skill"),
        )
    )["order_num"]
    rejected_num = (
        await _submit(
            service,
            _descriptor(
                upload_id=f"{2:032x}",
                skill_name="rejected-skill",
                digest="4" * 64,
            ),
        )
    )["order_num"]
    installed_num = (
        await _submit(
            service,
            _descriptor(
                upload_id=f"{3:032x}",
                skill_name="installed-skill",
                digest="5" * 64,
            ),
        )
    )["order_num"]
    await manager_api.handler.update(
        "approval_order", {"order_num": rejected_num}, {"status": "rejected"}
    )
    await manager_api.handler.update(
        "approval_order",
        {"order_num": installed_num},
        {"status": "approved", "result_data": {"install_status": "installed"}},
    )

    async def order_nums(scope: str) -> list[str]:
        listing = await service.list_mine(
            applicant_id="u_app",
            cluster_id=None,
            scope=scope,
            status=None,
            page=1,
            page_size=10,
        )
        return [item["order_num"] for item in listing["items"]]

    assert await order_nums("active") == [pending_num]
    assert set(await order_nums("history")) == {rejected_num, installed_num}
    assert set(await order_nums("all")) == {
        pending_num,
        rejected_num,
        installed_num,
    }


@pytest.mark.asyncio
async def test_list_mine_page_beyond_last_has_no_next(manager_api) -> None:
    """越界页返回空列表且 has_next=false，不重复回填上一页数据。"""
    service = SkillUploadService(manager_api.handler)
    await _submit(service, _descriptor())

    listing = await service.list_mine(
        applicant_id="u_app",
        cluster_id=None,
        scope="all",
        status=None,
        page=2,
        page_size=10,
    )

    assert listing["items"] == []
    assert listing["has_next"] is False


@pytest.mark.asyncio
async def test_upload_request_page_bounds_are_rejected(manager_api) -> None:
    """分页参数契约：page 从 1 开始，page_size 上限 50，越界由路由拒绝。"""
    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 51}):
        rejected = await manager_api.http.get(
            "/api/v1/user-console/skills/upload-requests",
            params={"scope": "all", **params},
        )
        assert rejected.status_code == 422, (params, rejected.text)
