"""企业版 Skill 按包上传审批：提交、列表、授权视图与安装结果。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.approval.service import (
    SKILL_UPLOAD,
    ApprovalOrderExpiredError,
    ApprovalService,
    _json_value,
)
from manager_server.core.skill_upload.approval import validate_package_info
from manager_server.core.skill_upload.errors import (
    SkillUploadConflictError,
    SkillUploadError,
    SkillUploadExpiredError,
    SkillUploadForbiddenError,
    SkillUploadNotFoundError,
    SkillUploadValidationError,
)
from manager_server.core.skill_upload.obs import fetch_package
from manager_server.core.skill_upload.retention import package_expires_at
from manager_server.infrastructure.utils import iso_datetime, utc_now

_ORDER_TABLE = "approval_order"
_SCAN_CHUNK = 100
_SCOPES = {"active", "history", "all"}
_VALID_STATUSES = {"pending", "approved", "rejected", "expired"}


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


class SkillUploadService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._approvals = ApprovalService(handler)

    # ── 提交 ──────────────────────────────────────────────
    async def submit(
        self,
        *,
        applicant_id: str,
        jiuwenclaw_id: str,
        group_id: str,
        bot_id: str,
        reason: str,
        approver_id: str,
        replace: bool,
        package_info: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = validate_package_info(package_info)
        apply_data = dict(descriptor)
        apply_data["replace"] = bool(replace)
        order_num = f"apr_{descriptor['upload_id']}"
        try:
            result = await self._approvals.submit_skill_upload(
                applicant_id=applicant_id,
                group_id=group_id,
                bot_id=bot_id,
                cluster_id=jiuwenclaw_id,
                reason=reason,
                approver_id=approver_id,
                apply_data=apply_data,
                order_num=order_num,
                expires_at=package_expires_at(descriptor.get("staged_at")),
            )
        except ApprovalOrderExpiredError as exc:
            raise SkillUploadExpiredError(str(exc)) from exc
        except PermissionError as exc:
            raise SkillUploadForbiddenError(str(exc)) from exc
        except ValueError as exc:
            # 指定审批人不合法或字段缺失都属于提交被拒，不泄露内部细节。
            raise SkillUploadForbiddenError(str(exc)) from exc
        return result

    # ── 我的上传 ──────────────────────────────────────────
    async def find_active(
        self,
        *,
        applicant_id: str,
        cluster_id: str,
        skill_name: str,
        digest: str,
    ) -> dict[str, Any] | None:
        """暂存后预检：同名或同包已有进行中单时直接返回，避免用户再选审批人。"""
        name = str(skill_name or "").strip()
        package_digest = str(digest or "").strip()
        if not name and not package_digest:
            raise SkillUploadValidationError("skill_name or digest is required")
        return await self._approvals.find_active_skill_upload(
            applicant_id=applicant_id,
            cluster_id=cluster_id,
            skill_name=name,
            digest=package_digest,
        )

    async def list_mine(
        self,
        *,
        applicant_id: str,
        cluster_id: str | None = None,
        scope: str,
        status: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        scope_value = str(scope or "all").strip() or "all"
        if scope_value not in _SCOPES:
            raise SkillUploadValidationError("scope must be active, history or all")
        if status is not None and status not in _VALID_STATUSES:
            raise SkillUploadValidationError("invalid approval status")
        if not 1 <= page_size <= 50:
            raise SkillUploadValidationError("page_size must be within 1..50")
        if page < 1:
            raise SkillUploadValidationError("page must be >= 1")

        filters: dict[str, Any] = {
            "applicant_id": applicant_id,
            "business_type": SKILL_UPLOAD,
            **({"cluster_id": cluster_id} if cluster_id else {}),
        }
        # 不统计精确总数：只扫描到当前页之后的一条，用 has_next 表示是否还有下一页。
        want = page * page_size + 1
        matched: list[Any] = []
        offset = 0
        while len(matched) < want:
            batch = await self._h.list_records(
                _ORDER_TABLE,
                filters,
                limit=_SCAN_CHUNK,
                offset=offset,
                order_by=[("created_at", True), ("id", True)],
            )
            if not batch:
                break
            batch = await self._approvals.refresh_skill_upload_expiry(batch)
            matched.extend(
                row for row in batch if _matches_list_filters(row, status, scope_value)
            )
            offset += len(batch)
            if len(batch) < _SCAN_CHUNK:
                break

        start = (page - 1) * page_size
        page_rows = matched[start:start + page_size]
        has_next = len(matched) > start + page_size
        comments = await self._approvals.latest_reject_comments(
            [str(_g(row, "order_num") or "") for row in page_rows]
        )
        items = [
            _item_out(row, comments.get(str(_g(row, "order_num") or ""), ""))
            for row in page_rows
        ]
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "has_next": has_next,
        }

    # ── 单据详情 / 授权视图 ────────────────────────────────
    async def get_order(
        self,
        *,
        order_num: str,
        viewer_id: str,
        view: str,
        jiuwenclaw_id: str | None,
    ) -> dict[str, Any]:
        order = await self._approvals.get_order(order_num)
        if order is None or str(order.get("business_type") or "") != SKILL_UPLOAD:
            raise SkillUploadNotFoundError("approval request not found")
        if str(order.get("applicant_id") or "") != viewer_id:
            raise SkillUploadForbiddenError("approval request belongs to another user")
        if await self._approvals.expire_skill_upload_order(order_num):
            order = await self._approvals.get_order(order_num)
            if order is None:
                raise SkillUploadNotFoundError("approval request not found")
        apply_data = order.get("apply_data")
        if not isinstance(apply_data, dict):
            raise SkillUploadValidationError("approval request has no package data")

        cluster = str(jiuwenclaw_id or "").strip()
        if view == "authorization":
            if not cluster:
                raise SkillUploadValidationError("jiuwenclaw_id is required")
            if cluster != str(order.get("cluster_id") or ""):
                raise SkillUploadForbiddenError(
                    "approval request belongs to another instance"
                )
            result_data = order.get("result_data")
            install_status = (
                str(result_data.get("install_status") or "")
                if isinstance(result_data, dict)
                else ""
            )
            return {
                "order_num": order.get("order_num"),
                "upload_id": apply_data.get("upload_id"),
                "skill_name": apply_data.get("skill_name"),
                "status": order.get("status"),
                "cluster_id": order.get("cluster_id"),
                "digest": apply_data.get("digest"),
                "replace": bool(apply_data.get("replace")),
                "install_status": install_status or None,
            }
        if view not in {"full", ""}:
            raise SkillUploadValidationError("view must be full or authorization")
        return order

    # ── 管理员下载原始包（manager 代理，浏览器不直连 OBS） ──
    async def read_package(self, *, order_num: str) -> tuple[str, bytes]:
        """按审批单里的 ``object_key`` 直读原始 ZIP，并校验元数据与内容 ``sha256``。

        浏览器不直连对象存储：内网 MinIO 常见 http，且集群 DNS 在浏览器侧不可解析。
        """
        order = await self._approvals.get_order(order_num)
        if order is None or str(order.get("business_type") or "") != SKILL_UPLOAD:
            raise SkillUploadNotFoundError("approval request not found")
        if _order_expired(order.get("expires_at")):
            raise SkillUploadExpiredError(
                "original package has expired; please upload again"
            )
        apply_data = order.get("apply_data")
        package = apply_data.get("package") if isinstance(apply_data, dict) else None
        if not isinstance(package, dict):
            raise SkillUploadValidationError("approval request has no package data")

        object_key = str(package.get("object_key") or "").strip()
        expected = str(package.get("sha256") or "").strip()
        if not object_key or not expected:
            raise SkillUploadValidationError("approval request has no package data")
        filename = str(package.get("filename") or "skill-package.zip")
        content = await fetch_package(object_key, expected)
        return filename, content

    # ── 安装结果回写（仅 Gateway） ─────────────────────────
    async def record_install_result(
        self,
        *,
        order_num: str,
        viewer_id: str,
        jiuwenclaw_id: str,
        upload_id: str,
        digest: str,
        status: str,
        detail: str | None,
    ) -> dict[str, Any]:
        if status not in {"installed", "failed"}:
            raise SkillUploadValidationError("status must be installed or failed")
        order = await self._approvals.get_order(order_num)
        if order is None or str(order.get("business_type") or "") != SKILL_UPLOAD:
            raise SkillUploadNotFoundError("approval request not found")
        if str(order.get("applicant_id") or "") != viewer_id:
            raise SkillUploadForbiddenError("approval request belongs to another user")
        if str(order.get("cluster_id") or "") != jiuwenclaw_id.strip():
            raise SkillUploadForbiddenError("approval request belongs to another instance")
        if str(order.get("status") or "") != "approved":
            raise SkillUploadConflictError("approval request is not approved")

        apply_data = order.get("apply_data")
        if not isinstance(apply_data, dict):
            raise SkillUploadValidationError("approval request has no package data")
        if str(apply_data.get("upload_id") or "") != upload_id:
            raise SkillUploadConflictError("upload_id does not match the request")
        if str(apply_data.get("digest") or "") != digest:
            raise SkillUploadConflictError("digest does not match the request")

        current = order.get("result_data")
        current_status = (
            str(current.get("install_status") or "")
            if isinstance(current, dict)
            else ""
        )
        if current_status == status:
            return {"order_num": order_num, "install_status": current_status}

        now = utc_now()
        result_data = {
            "install_status": status,
            "installed_at": iso_datetime(now) if status == "installed" else None,
            "detail": detail,
        }
        await self._approvals.append_record(
            order_num=order_num,
            operator_id=viewer_id,
            action="install",
            comment=detail,
            data={"result_data": result_data},
        )
        await self._h.update(
            _ORDER_TABLE,
            {"order_num": order_num},
            {
                "result_data": result_data,
                "updated_at": now,
                "updated_by": viewer_id,
            },
        )
        return {"order_num": order_num, "install_status": status}


def _order_expired(value: Any) -> bool:
    """``expires_at`` 为空表示永不过期（存量单据）。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return False
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _matches_scope(row: Any, scope: str) -> bool:
    if scope == "all":
        return True
    status = str(_g(row, "status") or "")
    result = _json_value(_g(row, "result_data"), {})
    install_status = (
        str(result.get("install_status") or "") if isinstance(result, dict) else ""
    )
    if scope == "active":
        return status == "pending" or (
            status == "approved" and install_status != "installed"
        )
    # history：已驳回、已过期或已安装
    return status in {"rejected", "expired"} or install_status == "installed"


def _matches_list_filters(row: Any, status: str | None, scope: str) -> bool:
    if status is not None and str(_g(row, "status") or "") != status:
        return False
    return _matches_scope(row, scope)


def _item_out(row: Any, reject_comment: str) -> dict[str, Any]:
    apply_data = _json_value(_g(row, "apply_data"), {})
    if not isinstance(apply_data, dict):
        apply_data = {}
    result_data = _json_value(_g(row, "result_data"), {})
    install_status = (
        str(result_data.get("install_status") or "")
        if isinstance(result_data, dict)
        else ""
    )
    expires_at_text = iso_datetime(_g(row, "expires_at"))
    install_detail = (
        str(result_data.get("detail") or "").strip() or None
        if isinstance(result_data, dict)
        else None
    )
    return {
        "order_num": _g(row, "order_num"),
        "upload_id": apply_data.get("upload_id"),
        "skill_name": apply_data.get("skill_name"),
        "skill_version": apply_data.get("skill_version"),
        "approver_id": _g(row, "approver_id"),
        "status": _g(row, "status"),
        "install_status": install_status or None,
        "install_detail": install_detail,
        "replace": bool(apply_data.get("replace")),
        "reason": _g(row, "reason") or "",
        "package_expires_at": expires_at_text,
        "latest_reject_comment": reject_comment,
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }
