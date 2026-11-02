"""Skill 上传资格：授权列、资格判定、申请与批准事务。"""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from sqlalchemy import inspect
from sqlalchemy.exc import SAWarning

from manager_server.core.approval import ApprovalService
from manager_server.core.approval.service import SKILL_UPLOAD_PERMISSION
from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.instance_access.instance_grant_service import (
    SUBJECT_ORG,
    SUBJECT_USER,
    ensure_instance_grant,
    pick_upload_grant,
)
from manager_server.models.table_init import init_all_tables

pytestmark = pytest.mark.unit

_CLUSTER = "inst-1"
_APPROVER = "admin2"


@pytest.fixture(autouse=True)
def _valid_approvers(monkeypatch: pytest.MonkeyPatch) -> None:
    """审批人校验读取 AuthzService；夹具里固定两名持 approval:act 的用户。"""
    from manager_server.core.authz import AuthzService

    monkeypatch.setattr(
        AuthzService,
        "list_users_with_permission",
        AsyncMock(return_value=["admin1", _APPROVER]),
    )


async def _open_handler(tmp_path: Path) -> SQLiteHandler:
    handler = SQLiteHandler(str(tmp_path / "skill_upload_ut.db"))
    await handler.init_database()
    await handler.connect()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        await init_all_tables(handler)
    return handler


class _Row:
    """pick_upload_grant 只依赖属性访问的轻量行。"""

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


def _row(
    subject_type: str,
    subject_id: str,
    *,
    upload: str = "deny",
    login: str = "allow",
    enabled: bool = True,
    expires_at: object = None,
) -> _Row:
    return _Row(
        subject_type=subject_type,
        subject_id=subject_id,
        upload_skill_policy=upload,
        login_policy=login,
        enabled=enabled,
        expires_at=expires_at,
    )


def test_pick_upload_grant_prefers_user_then_org_without_deny_veto() -> None:
    rows = [
        _row(SUBJECT_USER, "u1", upload="deny"),
        _row(SUBJECT_ORG, "org1", upload="allow"),
    ]
    assert pick_upload_grant(rows, user_id="u1", member_groups={"org1"}) == "org"
    assert pick_upload_grant(rows, user_id="u2", member_groups={"org1"}) == "org"
    assert pick_upload_grant(rows, user_id="u2", member_groups={"org2"}) == "none"


def test_pick_upload_grant_ignores_inactive_and_none_org() -> None:
    rows = [
        _row(SUBJECT_USER, "u1", upload="allow", enabled=False),
        _row(SUBJECT_ORG, "__none__", upload="allow"),
    ]
    assert pick_upload_grant(rows, user_id="u1", member_groups={"__none__"}) == "none"


@pytest.mark.asyncio
async def test_effective_upload_policy_requires_admission_then_any_allow(
    tmp_path: Path,
) -> None:
    handler = await _open_handler(tmp_path)
    try:
        svc = InstanceGrantService(handler)
        assert await svc.effective_upload_policy(_CLUSTER, "u1", {"org1"}) == {
            "allowed": False,
            "source": "none",
        }

        await ensure_instance_grant(
            handler, _CLUSTER, SUBJECT_ORG, "org1",
            login_policy="allow", upload_skill_policy="allow",
        )
        assert await svc.effective_upload_policy(_CLUSTER, "u1", {"org1"}) == {
            "allowed": True,
            "source": "org",
        }

        await ensure_instance_grant(
            handler, _CLUSTER, SUBJECT_USER, "u1",
            login_policy="allow", upload_skill_policy="allow",
        )
        assert await svc.effective_upload_policy(_CLUSTER, "u1", {"org1"}) == {
            "allowed": True,
            "source": "user",
        }

        await svc.update_grant(
            _CLUSTER, SUBJECT_USER, "u1", upload_skill_policy="deny"
        )
        # 用户行拒绝不否决组织行允许。
        assert await svc.effective_upload_policy(_CLUSTER, "u1", {"org1"}) == {
            "allowed": True,
            "source": "org",
        }
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_submit_reuses_pending_and_approve_grants_in_one_transaction(
    tmp_path: Path,
) -> None:
    handler = await _open_handler(tmp_path)
    try:
        svc = ApprovalService(handler)
        first = await svc.submit_skill_upload_permission(
            applicant_id="u1",
            group_id="org1",
            bot_id="b1",
            cluster_id=_CLUSTER,
            reason="需要上传业务报告技能",
            approver_id=_APPROVER,
        )
        second = await svc.submit_skill_upload_permission(
            applicant_id="u1",
            group_id="org1",
            bot_id="b1",
            cluster_id=_CLUSTER,
            reason="重复提交",
            approver_id=_APPROVER,
        )
        assert first["status"] == "pending"
        assert second["reused"] is True
        assert second["order_num"] == first["order_num"]

        order = await handler.get(
            "approval_order", {"order_num": first["order_num"]}
        )
        assert order is not None
        assert order.business_type == SKILL_UPLOAD_PERMISSION
        assert order.approver_id == _APPROVER

        result = await svc.act(
            order_num=first["order_num"],
            operator_id="admin2",
            action="approve",
            comment="同意",
        )
        assert result["status"] == "approved"
        assert result["result_data"]["grant_created"] is True

        grant = await handler.get(
            "instance_grant",
            {
                "jiuwenclaw_id": _CLUSTER,
                "subject_type": SUBJECT_USER,
                "subject_id": "u1",
            },
        )
        assert grant is not None
        assert grant.login_policy == "allow"
        assert grant.upload_skill_policy == "allow"

        records = await handler.list_records(
            "approval_record", {"order_num": first["order_num"]}, limit=10, offset=0
        )
        assert [getattr(record, "action") for record in records] == ["approve"]

        permission = await InstanceGrantService(handler).effective_upload_policy(
            _CLUSTER, "u1", {"org1"}
        )
        assert permission == {"allowed": True, "source": "user"}

        with pytest.raises(ValueError):
            await svc.act(
                order_num=first["order_num"],
                operator_id="admin3",
                action="approve",
                comment="重复批准",
            )
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_applicant_cannot_approve_own_request(tmp_path: Path) -> None:
    handler = await _open_handler(tmp_path)
    try:
        svc = ApprovalService(handler)
        submitted = await svc.submit_skill_upload_permission(
            applicant_id="u1",
            group_id="org1",
            bot_id="b1",
            cluster_id=_CLUSTER,
            reason="申请",
            approver_id=_APPROVER,
        )
        with pytest.raises(PermissionError):
            await svc.act(
                order_num=submitted["order_num"],
                operator_id="u1",
                action="approve",
                comment=None,
            )
        order = await handler.get(
            "approval_order", {"order_num": submitted["order_num"]}
        )
        assert order is not None
        assert order.status == "pending"
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_submit_rejects_self_or_unknown_approver(tmp_path: Path) -> None:
    handler = await _open_handler(tmp_path)
    try:
        svc = ApprovalService(handler)
        with pytest.raises(ValueError):
            await svc.submit_skill_upload_permission(
                applicant_id="u1",
                group_id="org1",
                bot_id="b1",
                cluster_id=_CLUSTER,
                reason="自批",
                approver_id="u1",
            )
        with pytest.raises(ValueError):
            await svc.submit_skill_upload_permission(
                applicant_id="u1",
                group_id="org1",
                bot_id="b1",
                cluster_id=_CLUSTER,
                reason="不在候选范围",
                approver_id="nobody",
            )
        rows = await handler.list_records("approval_order", {}, limit=10, offset=0)
        assert rows == []
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_only_designated_approver_can_act(tmp_path: Path) -> None:
    handler = await _open_handler(tmp_path)
    try:
        svc = ApprovalService(handler)
        submitted = await svc.submit_skill_upload_permission(
            applicant_id="u1",
            group_id="org1",
            bot_id="b1",
            cluster_id=_CLUSTER,
            reason="申请",
            approver_id=_APPROVER,
        )
        with pytest.raises(PermissionError):
            await svc.act(
                order_num=submitted["order_num"],
                operator_id="admin1",
                action="approve",
                comment=None,
            )
        result = await svc.act(
            order_num=submitted["order_num"],
            operator_id=_APPROVER,
            action="approve",
            comment="ok",
        )
        assert result["status"] == "approved"
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_existing_instance_grant_table_gains_upload_policy_column(
    tmp_path: Path,
) -> None:
    """存量表补列：只跑迁移函数，避免污染其它用例共享的声明式元数据。"""
    from manager_server.models.table_init import (
        _ensure_instance_grant_upload_skill_policy,
    )

    handler = SQLiteHandler(str(tmp_path / "legacy.db"))
    await handler.init_database()
    await handler.connect()
    try:
        engine = handler.get_engine()
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE TABLE instance_grant ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "jiuwenclaw_id VARCHAR(64) NOT NULL, "
                "subject_type VARCHAR(16) NOT NULL, "
                "subject_id VARCHAR(64) NOT NULL, "
                "granted_by VARCHAR(64), "
                "login_policy VARCHAR(16) NOT NULL DEFAULT 'allow', "
                "expires_at DATETIME, "
                "enabled BOOLEAN NOT NULL DEFAULT 1, "
                "data JSON, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        await _ensure_instance_grant_upload_skill_policy(handler)
        await _ensure_instance_grant_upload_skill_policy(handler)
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    item["name"] for item in inspect(sync).get_columns("instance_grant")
                }
            )
        assert "upload_skill_policy" in columns
    finally:
        await handler.disconnect()
