"""Skill 上传授权：管理员直授、审批写入与统一检查逻辑。"""

from __future__ import annotations

import pytest
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

from manager_server.core.approval.service import (
    SKILL_UPLOAD_PERMISSION,
    ApprovalService,
)
from manager_server.core.skill_upload import SkillUploadPermissionService
from manager_server.models.table_init import init_all_tables
from manager_server.schemas.skill_upload_schemas import SkillUploadGrantListQuery

pytestmark = [
    pytest.mark.asyncio,
    # sqlalchemy_handler 多 fixture 实例共用模块级 Base，重复注册同名 ORM 类触发
    # SAWarning；属良性（不影响功能），与 manager 测试套件 conftest 一致忽略。
    pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning"),
]


async def _handler(tmp_path) -> SQLiteHandler:
    handler = SQLiteHandler(str(tmp_path / "skill.db"))
    await handler.init_database()
    await handler.connect()
    await init_all_tables(handler)
    return handler


async def test_admin_grant_user_and_org(tmp_path):
    handler = await _handler(tmp_path)
    try:
        service = SkillUploadPermissionService(handler)
        await service.bind(cluster_id="jid-1", subject_type="user", ids=["u1"], operator_id="admin")
        assert await service.is_allowed(cluster_id="jid-1", user_id="u1", groups=[])
        assert not await service.is_allowed(cluster_id="jid-1", user_id="u2", groups=[])

        await service.bind(cluster_id="jid-1", subject_type="org", ids=["g1"], operator_id="admin")
        assert await service.is_allowed(cluster_id="jid-1", user_id="u2", groups=["g1"])
        assert not await service.is_allowed(cluster_id="jid-1", user_id="u2", groups=["g2"])

        result = await service.list_permissions(SkillUploadGrantListQuery())
        assert result["total"] == 2
    finally:
        await handler.disconnect()


async def test_revoke_keeps_approval_status_independent(tmp_path):
    handler = await _handler(tmp_path)
    try:
        service = SkillUploadPermissionService(handler)
        await service.bind(cluster_id="jid-1", subject_type="user", ids=["u1"], operator_id="admin")
        await service.revoke(cluster_id="jid-1", subject_type="user", ids=["u1"], operator_id="admin")
        assert not await service.is_allowed(cluster_id="jid-1", user_id="u1", groups=[])

        result = await service.list_permissions(SkillUploadGrantListQuery())
        assert result["items"][0]["enabled"] is False
    finally:
        await handler.disconnect()


async def test_approval_creates_shared_permission(tmp_path):
    handler = await _handler(tmp_path)
    try:
        service = SkillUploadPermissionService(handler)
        approval = ApprovalService(handler)

        submitted = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="g1",
            bot_id="bot-1",
            reason="需要上传团队技能",
            approver_id="admin",
        )
        assert submitted["status"] == "pending"
        order_num = submitted["order_num"]

        order = await approval.get_order(order_num)
        assert order["business_type"] == SKILL_UPLOAD_PERMISSION
        assert order["cluster_id"] == "jid-1"
        assert order["group_id"] == "g1"
        assert order["bot_id"] == "bot-1"
        assert order["apply_data"]["jiuwenclaw_id"] == "jid-1"

        # 重复申请复用同一张 pending 单
        again = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="g1",
            bot_id="bot-1",
            reason="再提交",
            approver_id="admin",
        )
        assert again["order_num"] == order_num

        acted = await approval.act(
            order_num=order_num, operator_id="admin", action="approve", comment="ok"
        )
        assert acted["status"] == "approved"
        assert acted["permission_id"] is not None

        effective = await service.effective(cluster_id="jid-1", user_id="u1", groups=[])
        assert effective["allowed"]
        assert effective["via"] == "user"
        permission = effective["permission"]
        assert permission["source"] == "approval"
        assert permission["source_order_num"] == order_num
        assert permission["enabled"] is True
        assert permission["updated_by"] == "admin"
        assert permission["updated_at"] is not None

        # 审批通过后管理员独立停用授权，审批状态不受影响
        updated = await service.update_permission(
            permission["id"],
            enabled=False,
            expires_at=None,
            clear_expires_at=False,
            operator_id="admin2",
        )
        assert updated["enabled"] is False
        assert not await service.is_allowed(cluster_id="jid-1", user_id="u1", groups=[])
        order_after = await approval.get_order(order_num)
        assert order_after["status"] == "approved"
        assert order_after["result_data"]["permission_id"] == permission["id"]
    finally:
        await handler.disconnect()


async def test_submit_already_granted_short_circuits(tmp_path):
    handler = await _handler(tmp_path)
    try:
        service = SkillUploadPermissionService(handler)
        await service.bind(cluster_id="jid-1", subject_type="user", ids=["u1"], operator_id="admin")
        result = await ApprovalService(handler).submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="",
            bot_id="bot-1",
            reason="already",
            approver_id="admin",
        )
        assert result == {"order_num": None, "status": "approved", "already_granted": True}
    finally:
        await handler.disconnect()


async def test_admin_api_and_user_console_permission(manager_api):
    from types import SimpleNamespace

    from manager_server.routers.auth_guards import get_current_user

    cid = await manager_api.create_instance()

    resp = await manager_api.http.post(
        "/api/v1/skills/upload-permissions",
        json={"cluster_id": cid, "subject_type": "user", "ids": ["u1"], "enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] == ["u1"]

    async def _user() -> SimpleNamespace:
        return SimpleNamespace(user_id="u1", is_admin=False, groups=[])

    manager_api.app.dependency_overrides[get_current_user] = _user
    permission = await manager_api.http.get(
        "/api/v1/user-console/skills/upload-permission",
        params={"context_id": cid},
    )
    assert permission.status_code == 200, permission.text
    data = permission.json()["data"]
    assert data["allowed"] is True
    assert data["via"] == "user"
    assert data["can_apply"] is False


async def test_user_console_apply_flow(manager_api, monkeypatch):
    from types import SimpleNamespace

    from manager_server.core.user_console import UserConsoleService
    from manager_server.routers.auth_guards import get_current_user

    async def _allowed(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(UserConsoleService, "user_can_access_context", _allowed)

    async def _user() -> SimpleNamespace:
        return SimpleNamespace(user_id="u2", is_admin=False, groups=["g1"])

    manager_api.app.dependency_overrides[get_current_user] = _user
    before = await manager_api.http.get(
        "/api/v1/user-console/skills/upload-permission",
        params={"context_id": "jid-1"},
    )
    assert before.json()["data"]["can_apply"] is True

    submitted = await manager_api.http.post(
        "/api/v1/user-console/skills/upload-permission-requests",
        json={
            "context_id": "jid-1",
            "group_id": "g1",
            "bot_id": "bot-1",
            "reason": "需要上传团队技能",
            "approver_id": "admin",
        },
    )
    assert submitted.status_code == 200, submitted.text
    order_num = submitted.json()["data"]["order_num"]
    assert order_num

    pending = await manager_api.http.get(
        "/api/v1/user-console/skills/upload-permission",
        params={"context_id": "jid-1"},
    )
    assert pending.json()["data"]["pending_order_num"] == order_num
    assert pending.json()["data"]["can_apply"] is False

    async def _admin() -> SimpleNamespace:
        return SimpleNamespace(user_id="admin", is_admin=False, groups=[])

    manager_api.app.dependency_overrides[get_current_user] = _admin
    acted = await manager_api.http.post(
        f"/api/v1/approvals/{order_num}/actions",
        json={"action": "approve", "comment": "ok"},
    )
    assert acted.status_code == 200, acted.text
    assert acted.json()["data"]["status"] == "approved"

    manager_api.app.dependency_overrides[get_current_user] = _user
    after = await manager_api.http.get(
        "/api/v1/user-console/skills/upload-permission",
        params={"context_id": "jid-1"},
    )
    assert after.json()["data"]["allowed"] is True


async def test_submit_reuses_rejected_order(tmp_path):
    """驳回后再次提交：复用同一张单回到 pending，不新建。"""
    handler = await _handler(tmp_path)
    try:
        approval = ApprovalService(handler)
        submitted = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="g1",
            bot_id="bot-1",
            reason="first",
            approver_id="admin",
        )
        order_num = submitted["order_num"]
        await approval.act(
            order_num=order_num, operator_id="admin", action="reject", comment="no"
        )

        # 驳回记录由审批通过写入的授权记录保持独立——这里驳回未写授权，清空以便复用判定
        again = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="g1",
            bot_id="bot-1",
            reason="second",
            approver_id="admin",
        )
        assert again["order_num"] == order_num
        assert again["status"] == "pending"
        order = await approval.get_order(order_num)
        assert order["status"] == "pending"
        assert order["reason"] == "second"

        # 仍然只有一张单
        mine = await approval.list_mine(applicant_id="u1")
        assert len(mine) == 1
    finally:
        await handler.disconnect()


async def test_submit_reuses_cancelled_order(tmp_path):
    """撤回后再次提交：复用最新 cancelled 单回到 pending。"""
    handler = await _handler(tmp_path)
    try:
        approval = ApprovalService(handler)
        submitted = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="",
            bot_id="bot-1",
            reason="first",
            approver_id="admin",
        )
        order_num = submitted["order_num"]
        await approval.cancel(order_num=order_num, operator_id="u1")

        again = await approval.submit_skill_upload_permission(
            applicant_id="u1",
            groups=[],
            cluster_id="jid-1",
            group_id="g1",
            bot_id="bot-1",
            reason="second",
            approver_id="admin",
        )
        assert again["order_num"] == order_num
        assert again["status"] == "pending"
        order = await approval.get_order(order_num)
        assert order["status"] == "pending"
        assert order["reason"] == "second"
        assert order["group_id"] == "g1"
    finally:
        await handler.disconnect()


async def test_concurrent_submit_creates_single_pending(tmp_path):
    """并发提交：事务化去重，最终只产生一张 pending 单。"""
    import asyncio

    handler = await _handler(tmp_path)
    try:
        approval = ApprovalService(handler)
        results = await asyncio.gather(
            *(
                approval.submit_skill_upload_permission(
                    applicant_id="u1",
                    groups=[],
                    cluster_id="jid-1",
                    group_id="g1",
                    bot_id="bot-1",
                    reason=f"r{i}",
                    approver_id="admin",
                )
                for i in range(5)
            )
        )
        order_nums = {item["order_num"] for item in results if item["order_num"]}
        assert len(order_nums) == 1
        mine = await approval.list_mine(applicant_id="u1")
        assert len(mine) == 1
    finally:
        await handler.disconnect()
