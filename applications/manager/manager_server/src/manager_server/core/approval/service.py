"""工作区配额扩容申请、审批状态机与批准后策略生效。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.foundation.log import get_logger
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from manager_server.core.quota.workspace_quota_policy import (
    APPROVAL_POLICY_PRIORITY,
    SOURCE_APPROVAL,
    _gateway_body,
)
from manager_server.infrastructure.utils import iso_datetime, new_uuid4, strip_optional, utc_now
from manager_server.manager_config_push import gateway_request
from manager_server.models.approval_models import (
    APPROVAL_ORDER_TABLE_DEF,
    APPROVAL_RECORD_TABLE_DEF,
)
from manager_server.models.quota_models import WORKSPACE_QUOTA_POLICY_TABLE_DEF

_ORDER_TABLE = APPROVAL_ORDER_TABLE_DEF.table_name
_RECORD_TABLE = APPROVAL_RECORD_TABLE_DEF.table_name
_POLICY_TABLE = WORKSPACE_QUOTA_POLICY_TABLE_DEF.table_name
_CAP = 100_000
_SUBMIT_TRANSACTION_ATTEMPTS = 5
_log = get_logger(__name__)

WORKSPACE_QUOTA_EXPAND = "workspace_quota_expand"
VALID_STATUSES = frozenset({"pending", "approved", "rejected", "cancelled"})
VALID_ACTIONS = frozenset({"approve", "reject"})
_FALLBACK_LIMIT_BYTES = 10 * 1024**4
_FALLBACK_POLICY_ID = "local_default"


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            # JSON columns may legitimately contain a scalar string, which is
            # the documented representation for a single match expression.
            return value
    return value


def _escape_expr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _personal_match_expr(applicant_id: str, group_id: str, bot_id: str) -> str:
    return (
        f"user_id == '{_escape_expr_value(applicant_id)}' and "
        f"group_id == '{_escape_expr_value(group_id)}' and "
        f"bot_id == '{_escape_expr_value(bot_id)}'"
    )


def _hashed_workspace_key(group_id: str, bot_id: str, user_id: str) -> str:
    import hashlib

    logical = f"{group_id}{bot_id}{user_id}".strip() or "default_workspace_key"
    return hashlib.md5(logical.encode("utf-8")).hexdigest()


def _record_out(row: Any) -> dict[str, Any]:
    return {
        "record_id": _g(row, "record_id"),
        "order_num": _g(row, "order_num"),
        "operator_id": _g(row, "operator_id"),
        "action": _g(row, "action"),
        "comment": _g(row, "comment"),
        "created_at": iso_datetime(_g(row, "created_at")),
    }


def _order_out(row: Any, *, detail: bool = False) -> dict[str, Any]:
    output = {
        "cluster_id": _g(row, "cluster_id"),
        "order_num": _g(row, "order_num"),
        "business_type": _g(row, "business_type"),
        "title": _g(row, "title"),
        "status": _g(row, "status"),
        "applicant_id": _g(row, "applicant_id"),
        "approver_id": _g(row, "approver_id"),
        "group_id": _g(row, "group_id") or "",
        "bot_id": _g(row, "bot_id"),
        "reason": _g(row, "reason") or "",
        "created_at": iso_datetime(_g(row, "created_at")),
        "updated_at": iso_datetime(_g(row, "updated_at")),
    }
    if detail:
        output.update(
            {
                "apply_data": _json_value(_g(row, "apply_data"), {}),
                "result_data": _json_value(_g(row, "result_data"), None),
                "finished_at": iso_datetime(_g(row, "finished_at")),
            }
        )
    return output


def _is_todo_for_operator(row: Any, operator_id: str) -> bool:
    if _g(row, "applicant_id") == operator_id:
        return False
    return str(_g(row, "approver_id") or "").strip() == operator_id


def _order_search_blob(row: Any) -> str:
    return " ".join(
        [
            str(_g(row, "order_num") or ""),
            str(_g(row, "title") or ""),
            str(_g(row, "reason") or ""),
        ]
    ).lower()


def _filter_orders_by_search(rows: list[Any], search: str | None) -> list[Any]:
    needle = (search or "").strip().lower()
    if not needle:
        return rows
    matched: list[Any] = []
    for row in rows:
        if needle in _order_search_blob(row):
            matched.append(row)
    return matched


def _find_enabled_policy_by_match(rows: list[Any], match_expr: Any) -> Any | None:
    for row in rows:
        if not bool(_g(row, "enabled", True)):
            continue
        if _json_value(_g(row, "match_expr"), []) == match_expr:
            return row
    return None


def _policy_out(row: Any) -> dict[str, Any]:
    desc = _g(row, "policy_desc")
    if isinstance(desc, str):
        desc = desc.strip() or None
    return {
        "policy_id": _g(row, "policy_id"),
        "cluster_id": _g(row, "cluster_id"),
        "policy_name": str(_g(row, "policy_name") or "").strip(),
        "policy_desc": desc,
        "match_expr": _json_value(_g(row, "match_expr"), []),
        "priority": int(_g(row, "priority", 0)),
        "limit_bytes": int(_g(row, "limit_bytes", 0)),
        "soft_percent": int(_g(row, "soft_percent", 80)),
        "hard_percent": int(_g(row, "hard_percent", 100)),
        "source": str(_g(row, "source") or "").strip() or (
            "approval" if _g(row, "source_order_num") else "manual"
        ),
        "source_order_num": _g(row, "source_order_num"),
        "enabled": bool(_g(row, "enabled", True)),
    }


def _is_retryable_transaction_error(exc: DBAPIError) -> bool:
    """识别并发提交时数据库返回的可重试事务冲突。"""
    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate in {"40001", "40P01"}:  # serialization failure / deadlock
        return True

    error_number = getattr(original, "errno", None)
    if error_number is None:
        args = getattr(original, "args", ())
        if args and isinstance(args[0], int):
            error_number = args[0]
    if error_number in {1205, 1213}:  # MySQL lock timeout / deadlock
        return True

    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


class ApprovalService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def submit_workspace_quota_expand(
        self,
        *,
        applicant_id: str,
        group_id: str,
        bot_id: str,
        cluster_id: str,
        workspace_key: str,
        current_limit_bytes: int,
        requested_limit_bytes: int,
        used_bytes: int,
        source_policy_id: str,
        reason: str,
        approver_id: str,
    ) -> dict[str, Any]:
        """提交扩容；配额快照应由服务端解析，不得信任浏览器伪造的 limit / policy。"""
        applicant_id = applicant_id.strip()
        group_id = group_id.strip()
        bot_id = bot_id.strip()
        cluster_id = cluster_id.strip()
        workspace_key = workspace_key.strip()
        source_policy_id = source_policy_id.strip()
        reason = reason.strip()
        approver_id = approver_id.strip()
        if not all(
            (applicant_id, bot_id, cluster_id, workspace_key, source_policy_id, reason, approver_id)
        ):
            raise ValueError(
                "applicant, bot, cluster, workspace, source policy, reason and approver are required"
            )
        if current_limit_bytes < 0:
            raise ValueError("unlimited quota does not need expansion")
        if requested_limit_bytes <= current_limit_bytes:
            raise ValueError("requested_limit_bytes must be greater than current_limit_bytes")
        if used_bytes < 0:
            raise ValueError("used_bytes must be greater than or equal to 0")
        if approver_id == applicant_id:
            raise ValueError("applicant cannot designate self as approver")
        await self._ensure_valid_approver(approver_id)

        usage_percent = (
            used_bytes / current_limit_bytes * 100 if current_limit_bytes > 0 else 100.0
        )
        apply_data = {
            "workspace_key": workspace_key,
            "current_limit_bytes": current_limit_bytes,
            "requested_limit_bytes": requested_limit_bytes,
            "used_bytes": used_bytes,
            "usage_percent": usage_percent,
            "source_policy_id": source_policy_id,
        }
        return await self._submit_workspace_quota_expand_transactionally(
            applicant_id=applicant_id,
            group_id=group_id,
            bot_id=bot_id,
            cluster_id=cluster_id,
            reason=reason,
            apply_data=apply_data,
            approver_id=approver_id,
        )

    async def submit_from_user_console(
        self,
        *,
        applicant_id: str,
        group_id: str,
        bot_id: str,
        cluster_id: str,
        requested_limit_bytes: int,
        reason: str,
        used_bytes: int | None = None,
        approver_id: str,
    ) -> dict[str, Any]:
        """用户面提交：服务端解析生效配额与 workspace_key；used_bytes 可缺省为 0。"""
        from manager_server.core.quota import WorkspaceQuotaPolicyService

        effective = await WorkspaceQuotaPolicyService(self._h).effective(
            cluster_id=cluster_id,
            user_id=applicant_id,
            group_id=group_id,
            bot_id=bot_id,
        )
        if effective is None:
            current_limit_bytes = _FALLBACK_LIMIT_BYTES
            source_policy_id = _FALLBACK_POLICY_ID
        else:
            current_limit_bytes = int(effective.get("limit_bytes") or 0)
            source_policy_id = str(effective.get("source_policy_id") or "").strip()
            if not source_policy_id:
                source_policy_id = _FALLBACK_POLICY_ID
        return await self.submit_workspace_quota_expand(
            applicant_id=applicant_id,
            group_id=group_id,
            bot_id=bot_id,
            cluster_id=cluster_id,
            workspace_key=_hashed_workspace_key(group_id, bot_id, applicant_id),
            current_limit_bytes=current_limit_bytes,
            requested_limit_bytes=requested_limit_bytes,
            used_bytes=int(used_bytes or 0),
            source_policy_id=source_policy_id,
            reason=reason,
            approver_id=approver_id,
        )

    async def _submit_workspace_quota_expand_transactionally(
        self,
        *,
        applicant_id: str,
        group_id: str,
        bot_id: str,
        cluster_id: str,
        reason: str,
        apply_data: dict[str, Any],
        approver_id: str,
    ) -> dict[str, Any]:
        """串行化同一申请键的检查与写入，避免并发创建重复未结束单。"""
        if not isinstance(self._h, SQLAlchemyHandler):
            raise TypeError("approval submission requires a SQLAlchemy database handler")
        engine = self._h.get_engine()
        if engine is None:
            raise RuntimeError("database must be connected before submitting approval")
        table = self._h.get_table(_ORDER_TABLE)
        dialect = engine.dialect.name

        for attempt in range(_SUBMIT_TRANSACTION_ATTEMPTS):
            try:
                async with engine.connect() as connection:
                    if dialect == "sqlite":
                        # SQLite 的默认延迟事务会让两个请求同时读到“无记录”。
                        # BEGIN IMMEDIATE 在读取前取得数据库写锁，使检查和写入成为原子操作。
                        await connection.exec_driver_sql("BEGIN IMMEDIATE")
                        try:
                            result = await self._submit_workspace_quota_expand_in_transaction(
                                connection,
                                table,
                                applicant_id=applicant_id,
                                group_id=group_id,
                                bot_id=bot_id,
                                cluster_id=cluster_id,
                                reason=reason,
                                apply_data=apply_data,
                                approver_id=approver_id,
                            )
                        except BaseException:
                            await connection.rollback()
                            raise
                        await connection.commit()
                        return result

                    serial_connection = await connection.execution_options(
                        isolation_level="SERIALIZABLE"
                    )
                    async with serial_connection.begin():
                        result = await self._submit_workspace_quota_expand_in_transaction(
                            serial_connection,
                            table,
                            applicant_id=applicant_id,
                            group_id=group_id,
                            bot_id=bot_id,
                            cluster_id=cluster_id,
                            reason=reason,
                            apply_data=apply_data,
                            approver_id=approver_id,
                        )
                    return result
            except DBAPIError as exc:
                if (
                    attempt + 1 >= _SUBMIT_TRANSACTION_ATTEMPTS
                    or not _is_retryable_transaction_error(exc)
                ):
                    raise
                await asyncio.sleep(0.02 * (2**attempt))

        raise RuntimeError("approval submission transaction retries exhausted")

    @staticmethod
    async def _submit_workspace_quota_expand_in_transaction(
        connection: AsyncConnection,
        table: Any,
        *,
        applicant_id: str,
        group_id: str,
        bot_id: str,
        cluster_id: str,
        reason: str,
        apply_data: dict[str, Any],
        approver_id: str,
    ) -> dict[str, Any]:
        query = (
            select(table)
            .where(
                table.c.applicant_id == applicant_id,
                table.c.cluster_id == cluster_id,
                table.c.business_type == WORKSPACE_QUOTA_EXPAND,
                table.c.status.in_(["pending", "rejected", "cancelled"]),
            )
            .order_by(table.c.id)
            .with_for_update()
        )
        unfinished = (await connection.execute(query)).mappings().all()
        pending = next((row for row in unfinished if row["status"] == "pending"), None)
        if pending is not None:
            return {"order_num": pending["order_num"], "status": "pending"}

        now = utc_now()
        # 驳回或撤回后补正：复用同一张单回到 pending（优先 rejected，否则取最新 cancelled）
        rejected = next((row for row in unfinished if row["status"] == "rejected"), None)
        cancelled = next(
            (row for row in reversed(unfinished) if row["status"] == "cancelled"),
            None,
        )
        reusable = rejected if rejected is not None else cancelled
        if reusable is not None:
            order_num = str(reusable["order_num"])
            await connection.execute(
                update(table)
                .where(table.c.order_num == order_num)
                .values(
                    approver_id=approver_id,
                    status="pending",
                    finished_at=None,
                    reason=reason,
                    apply_data=apply_data,
                    result_data=None,
                    updated_at=now,
                    updated_by=applicant_id,
                )
            )
            return {"order_num": order_num, "status": "pending"}

        order_num = f"apr_{new_uuid4().replace('-', '')}"
        await connection.execute(
            insert(table).values(
                cluster_id=cluster_id,
                order_num=order_num,
                title="工作区扩容",
                applicant_id=applicant_id,
                approver_id=approver_id,
                group_id=group_id,
                bot_id=bot_id,
                status="pending",
                finished_at=None,
                business_type=WORKSPACE_QUOTA_EXPAND,
                reason=reason,
                apply_data=apply_data,
                result_data=None,
                data=None,
                created_at=now,
                created_by=applicant_id,
                updated_at=now,
                updated_by=applicant_id,
            )
        )
        return {"order_num": order_num, "status": "pending"}

    async def list_orders(
        self,
        *,
        operator_id: str,
        business_type: str | None,
        view: str | None,
        status: str | None,
        group_id: str | None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        if view not in {None, "todo", "mine"}:
            raise ValueError("view must be todo or mine")
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        filters: dict[str, Any] = {}
        if business_type:
            filters["business_type"] = business_type
        if status:
            filters["status"] = status
        if group_id is not None:
            filters["group_id"] = group_id
        if view == "mine":
            filters["applicant_id"] = operator_id
        rows = await self._h.list_records(_ORDER_TABLE, filters, limit=_CAP, offset=0)
        if view == "todo":
            # 管理面：审批人是当前用户的全部单据（可用 status 再筛；不含本人申请）
            todo_rows: list[Any] = []
            for row in rows:
                if _is_todo_for_operator(row, operator_id):
                    todo_rows.append(row)
            rows = todo_rows
        rows = _filter_orders_by_search(rows, search)
        rows.sort(key=lambda row: _g(row, "created_at"), reverse=True)
        return [_order_out(row) for row in rows]

    async def list_mine(
        self,
        *,
        applicant_id: str,
        business_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """申请人侧「我的申请」列表（含 current_approvers）；可按业务类型/状态/关键字过滤。

        ``search`` 对单号、标题、申请说明做不区分大小写的子串匹配。
        """
        applicant_id = applicant_id.strip()
        if not applicant_id:
            raise ValueError("applicant_id is required")
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        filters: dict[str, Any] = {"applicant_id": applicant_id}
        if business_type and business_type.strip():
            filters["business_type"] = business_type.strip()
        if status:
            filters["status"] = status
        rows = await self._h.list_records(_ORDER_TABLE, filters, limit=_CAP, offset=0)
        rows = _filter_orders_by_search(rows, search)
        rows.sort(key=lambda row: _g(row, "created_at"), reverse=True)
        pool = await self._current_approvers(exclude_user_id=applicant_id)
        pool_by_id = {item["user_id"]: item for item in pool}
        reject_comments = await self._latest_reject_comments(
            [
                str(_g(row, "order_num") or "")
                for row in rows
                if str(_g(row, "status") or "") == "rejected"
            ]
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            row_status = str(_g(row, "status") or "")
            order_num = str(_g(row, "order_num") or "")
            designated = str(_g(row, "approver_id") or "").strip()
            if row_status == "pending":
                if designated and designated in pool_by_id:
                    current_approvers = [pool_by_id[designated]]
                elif designated:
                    current_approvers = [{"user_id": designated, "display_name": designated}]
                else:
                    current_approvers = list(pool)
            else:
                current_approvers = []
            items.append(
                {
                    "order_num": order_num,
                    "cluster_id": _g(row, "cluster_id"),
                    "business_type": _g(row, "business_type"),
                    "title": _g(row, "title"),
                    "status": row_status,
                    "reason": _g(row, "reason") or "",
                    "applicant_id": _g(row, "applicant_id"),
                    "approver_id": designated or None,
                    "group_id": _g(row, "group_id") or "",
                    "bot_id": _g(row, "bot_id"),
                    "apply_data": _json_value(_g(row, "apply_data"), {}),
                    "result_data": _json_value(_g(row, "result_data"), None),
                    "latest_reject_comment": reject_comments.get(order_num) or "",
                    "current_approvers": current_approvers,
                    "created_at": iso_datetime(_g(row, "created_at")),
                    "updated_at": iso_datetime(_g(row, "updated_at")),
                }
            )
        return items

    async def list_approver_candidates(self, *, exclude_user_id: str) -> list[dict[str, str]]:
        """用户面下拉：可选审批人（持有 approval:act，排除当前用户）。"""
        return await self._current_approvers(exclude_user_id=exclude_user_id)

    async def _ensure_valid_approver(self, approver_id: str) -> None:
        from manager_server.core.authz import AuthzService

        allowed = await AuthzService(self._h).list_users_with_permission("approval:act")
        if approver_id not in set(allowed):
            raise ValueError("approver must hold approval:act permission")

    async def _latest_reject_comments(self, order_nums: list[str]) -> dict[str, str]:
        """取各单最近一次驳回意见，供申请人补正时参考。"""
        comments: dict[str, str] = {}
        for order_num in order_nums:
            if not order_num or order_num in comments:
                continue
            records = await self._h.list_records(
                _RECORD_TABLE, {"order_num": order_num}, limit=_CAP, offset=0
            )
            reject_records = [
                item for item in records if str(_g(item, "action") or "") == "reject"
            ]
            if not reject_records:
                comments[order_num] = ""
                continue
            reject_records.sort(key=lambda item: _g(item, "created_at") or "", reverse=True)
            comments[order_num] = str(_g(reject_records[0], "comment") or "").strip()
        return comments

    async def list_mine_workspace_quota_expand(
        self,
        *,
        applicant_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """兼容旧调用：仅返回工作区扩容申请。"""
        return await self.list_mine(
            applicant_id=applicant_id,
            business_type=WORKSPACE_QUOTA_EXPAND,
            status=status,
        )

    async def _current_approvers(self, *, exclude_user_id: str) -> list[dict[str, str]]:
        from manager_server.core.authz import AuthzService

        user_ids = await AuthzService(self._h).list_users_with_permission("approval:act")
        names = await self._lookup_display_names(user_ids)
        return [
            {
                "user_id": user_id,
                "display_name": names.get(user_id) or user_id,
            }
            for user_id in user_ids
            if user_id and user_id != exclude_user_id
        ]

    @staticmethod
    async def _lookup_display_names(user_ids: list[str]) -> dict[str, str]:
        """尽力从 identity 拉取 display_name；失败时调用方回退到 user_id。"""
        if not user_ids:
            return {}
        try:
            import httpx

            from manager_server.infrastructure.config import settings
        except Exception:  # noqa: BLE001
            return {}
        public_key = str(settings.identity_public_key_url or "").rstrip("/")
        suffix = "/v1/auth/public_key"
        if public_key.endswith(suffix):
            base = public_key[: -len(suffix)]
        else:
            base = str(settings.manager_web_idp_target or "").rstrip("/")
        if not base:
            return {}
        names: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                for user_id in user_ids:
                    try:
                        resp = await client.get(f"{base}/v1/users/{user_id}")
                    except Exception:  # noqa: BLE001
                        continue
                    if resp.status_code != 200:
                        continue
                    try:
                        body = resp.json()
                    except ValueError:
                        continue
                    if not isinstance(body, dict):
                        continue
                    name = str(body.get("display_name") or "").strip()
                    if name:
                        names[user_id] = name
        except Exception:  # noqa: BLE001
            return names
        return names

    async def get_order(self, order_num: str) -> dict[str, Any] | None:
        row = await self._h.get(_ORDER_TABLE, {"order_num": order_num})
        if row is None:
            return None
        records = await self._h.list_records(
            _RECORD_TABLE, {"order_num": order_num}, limit=_CAP, offset=0
        )
        records.sort(key=lambda item: _g(item, "created_at"))
        return {**_order_out(row, detail=True), "records": [_record_out(item) for item in records]}

    async def _append_record(
        self,
        *,
        order_num: str,
        operator_id: str,
        action: str,
        comment: str | None,
        data: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        await self._h.create(
            _RECORD_TABLE,
            {
                "record_id": f"arr_{new_uuid4().replace('-', '')}",
                "order_num": order_num,
                "operator_id": operator_id,
                "action": action,
                "comment": strip_optional(comment),
                "data": data,
                "created_at": now,
                "created_by": operator_id,
                "updated_at": now,
                "updated_by": operator_id,
            },
        )

    async def act(
        self,
        *,
        order_num: str,
        operator_id: str,
        action: str,
        comment: str | None,
    ) -> dict[str, Any] | None:
        if action not in VALID_ACTIONS:
            raise ValueError("action must be approve or reject")
        row = await self._h.get(_ORDER_TABLE, {"order_num": order_num})
        if row is None:
            return None
        status = str(_g(row, "status"))
        result_data = _json_value(_g(row, "result_data"), {})
        is_sync_retry = (
            action == "approve"
            and status == "approved"
            and result_data.get("sync_status") == "failed"
        )
        if status != "pending" and not is_sync_retry:
            raise ValueError("only pending approval or failed approved sync can be acted on")
        if _g(row, "applicant_id") == operator_id:
            raise PermissionError("applicant cannot approve or reject own request")
        designated = str(_g(row, "approver_id") or "").strip()
        if status == "pending" and designated and designated != operator_id:
            raise PermissionError("only the designated approver can act on this request")

        if action == "reject":
            await self._append_record(
                order_num=order_num,
                operator_id=operator_id,
                action="reject",
                comment=comment,
            )
            await self._h.update(
                _ORDER_TABLE,
                {"order_num": order_num},
                {
                    "status": "rejected",
                    "approver_id": operator_id,
                    "finished_at": None,
                    "result_data": None,
                    "updated_at": utc_now(),
                    "updated_by": operator_id,
                },
            )
            return {"order_num": order_num, "status": "rejected", "policy_id": None}

        policy, sync_result = await self._apply_workspace_quota(row, operator_id)
        now = utc_now()
        await self._append_record(
            order_num=order_num,
            operator_id=operator_id,
            action="approve",
            comment=comment,
            data={"result_data": sync_result},
        )
        await self._h.update(
            _ORDER_TABLE,
            {"order_num": order_num},
            {
                "status": "approved",
                "approver_id": _g(row, "approver_id") if is_sync_retry else operator_id,
                "finished_at": now,
                "result_data": sync_result,
                "updated_at": now,
                "updated_by": operator_id,
            },
        )
        return {
            "order_num": order_num,
            "status": "approved",
            "policy_id": policy["policy_id"],
        }

    async def cancel(self, *, order_num: str, operator_id: str) -> dict[str, Any] | None:
        row = await self._h.get(_ORDER_TABLE, {"order_num": order_num})
        if row is None:
            return None
        if _g(row, "applicant_id") != operator_id:
            raise PermissionError("only applicant can cancel request")
        if _g(row, "status") != "pending":
            raise ValueError("only pending request can be cancelled")
        await self._append_record(
            order_num=order_num,
            operator_id=operator_id,
            action="cancel",
            comment=None,
        )
        now = utc_now()
        await self._h.update(
            _ORDER_TABLE,
            {"order_num": order_num},
            {
                "status": "cancelled",
                "approver_id": None,
                "finished_at": now,
                "result_data": None,
                "updated_at": now,
                "updated_by": operator_id,
            },
        )
        return {"order_num": order_num, "status": "cancelled"}

    async def _apply_workspace_quota(
        self, order: Any, operator_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        apply_data = _json_value(_g(order, "apply_data"), {})
        current = int(apply_data.get("current_limit_bytes") or 0)
        requested = int(apply_data.get("requested_limit_bytes") or 0)
        if current < 0 or requested <= current:
            raise ValueError("requested_limit_bytes must be greater than current_limit_bytes")
        cluster_id = str(_g(order, "cluster_id"))
        order_num = str(_g(order, "order_num"))
        match_expr = _personal_match_expr(
            str(_g(order, "applicant_id")),
            str(_g(order, "group_id") or ""),
            str(_g(order, "bot_id")),
        )
        rows = await self._h.list_records(
            _POLICY_TABLE, {"cluster_id": cluster_id}, limit=_CAP, offset=0
        )
        existing = _find_enabled_policy_by_match(rows, match_expr)
        now = utc_now()
        if existing is not None:
            policy_id = str(_g(existing, "policy_id"))
            before_policy = _policy_out(existing)
            await self._h.update(
                _POLICY_TABLE,
                {"policy_id": policy_id},
                {
                    "limit_bytes": requested,
                    "priority": APPROVAL_POLICY_PRIORITY,
                    "source": SOURCE_APPROVAL,
                    "source_order_num": order_num,
                    "updated_at": now,
                    "updated_by": operator_id,
                },
            )
        else:
            before_policy = None
            policy_id = f"qp_{new_uuid4().replace('-', '')}"
            await self._h.create(
                _POLICY_TABLE,
                {
                    "cluster_id": cluster_id,
                    "policy_id": policy_id,
                    "policy_name": f"quota-expand-{order_num}",
                    "policy_desc": "Created by workspace quota approval",
                    "match_expr": match_expr,
                    "priority": APPROVAL_POLICY_PRIORITY,
                    "limit_bytes": requested,
                    "soft_percent": 80,
                    "hard_percent": 100,
                    "source": SOURCE_APPROVAL,
                    "source_order_num": order_num,
                    "enabled": True,
                    "data": None,
                    "created_at": now,
                    "created_by": operator_id,
                    "updated_at": now,
                    "updated_by": operator_id,
                },
            )
        stored = await self._h.get(_POLICY_TABLE, {"policy_id": policy_id})
        policy = _policy_out(stored)
        _log.info(
            "workspace quota policy changed operator_id=%s policy_id=%s "
            "order_num=%s before=%s after=%s",
            operator_id,
            policy_id,
            order_num,
            before_policy,
            policy,
        )
        # Gateway PUT 与管理面手写策略共用同一 body 契约（含必填 policy_name）。
        if not policy.get("policy_name"):
            policy["policy_name"] = f"quota-expand-{order_num}"
        gateway_body = _gateway_body(policy)
        try:
            ack = await gateway_request(
                cluster_id,
                "PUT",
                "/api/v1/workspace-quota/policies",
                gateway_body,
            )
            sync_status = "success"
            sync_detail = json.dumps(ack, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 - sync failure is persisted in result_data
            sync_status = "failed"
            sync_detail = str(exc)
        result = {
            "policy_id": policy_id,
            "limit_bytes": requested,
            "sync_status": sync_status,
            "sync_detail": sync_detail,
        }
        return policy, result
