"""用户控制台业务逻辑：当前用户可访问的 Agent 上下文组合。

instance_grant 在 core/instance_access；instance_agent_resource 在 core/instance_resource。
返回 (bot_id, group_id, user_id, jiuwenclaw_id) 组合；任选其一即可访问对应 Agent。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.instance_access.instance_grant_service import SUBJECT_ORG, SUBJECT_USER
from manager_server.core.instance_resource.runtime_config_sync import (
    build_service_scope_index,
    resolve_service_template_id,
)
from manager_server.core.user_console.agent_identity import try_build_agent_identity
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.match_expr import evaluate_match_expr
from manager_server.infrastructure.utils import utc_now
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF
from manager_server.models.agent_definition_models import AGENT_DEFINITION_TABLE_DEF
from manager_server.models.template_models import AGENT_TEMPLATE_TABLE_DEF

_GRANT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_AGENT_DEFINITION = AGENT_DEFINITION_TABLE_DEF.table_name
_AGENT_TEMPLATE = AGENT_TEMPLATE_TABLE_DEF.table_name
_INSTANCE_INFO = "instance_info"
_CAP = 100_000
_NO_ORG_GROUP_ID = "__none__"


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


def _grant_expired(expires_at: Any) -> bool:
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utc_now()


def _identity_base_url() -> str:
    public_key = str(settings.identity_public_key_url or "").rstrip("/")
    suffix = "/v1/auth/public_key"
    if public_key.endswith(suffix):
        return public_key[: -len(suffix)]
    return str(settings.manager_web_idp_target or "").rstrip("/")


async def _load_orgs(authorization: str | None) -> list[dict[str, str]]:
    """一次拉取用户组织：每项含 group_id + group_name（display_name）。失败返回空。"""
    token = (authorization or "").strip()
    base = _identity_base_url()
    if not token or not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(
                f"{base}/v1/auth/me/orgs",
                headers={"Authorization": token},
            )
        if resp.status_code != 200:
            return []
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    raw = body.get("orgs") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id") or "").strip()
        if not gid:
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip() or gid
        out.append({"group_id": gid, "group_name": name})
    return out


class UserConsoleService:
    """按 instance_grant + instance_agent_resource 枚举当前用户可访问的上下文。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._instance_grants = InstanceGrantService(handler)

    async def list_accessible_contexts(
        self,
        user_id: str,
        groups: list[str] | None = None,
        *,
        authorization: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回用户可选用的上下文列表（兼容旧调用方）。"""
        contexts, _ = await self._list_accessible_contexts(
            user_id, groups, authorization=authorization
        )
        return contexts

    async def list_accessible_contexts_with_meta(
        self,
        user_id: str,
        groups: list[str] | None = None,
        *,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        """返回上下文列表与空态原因。

        ``empty_reason``：``no_grant``（无授权/无匹配）或
        ``no_agent_template``（有授权但管理面未新增 Agent 模板）。
        """
        contexts, matched = await self._list_accessible_contexts(
            user_id, groups, authorization=authorization
        )
        empty_reason = None
        if not contexts:
            empty_reason = "no_agent_template" if matched else "no_grant"
        return {"contexts": contexts, "empty_reason": empty_reason}

    async def _list_accessible_contexts(
        self,
        user_id: str,
        groups: list[str] | None = None,
        *,
        authorization: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """返回 ``(上下文列表, 过滤前有授权的组合数)``。

        业务键均为 id：``bot_id`` / ``group_id`` / ``user_id`` / ``jiuwenclaw_id``。

        ``agent_id`` 由 ``runtime_template_id`` + ``agent_template_id`` 经 base62
        可逆编码得到（43 字符，见 ``agent_identity`` 模块）；只返回已在管理面
        Agent 模板（``agent_definition``）中配置过的组合，名称取该表中的名称。
        未配置的组合不展示；运行时模板按 ``(priority ASC, scope_id ASC)``
        first-fit 解析，与 Runtime 选路一致。
        """
        uid = str(user_id or "").strip()
        if not uid:
            return [], 0

        jwt_groups = {
            str(item).strip() for item in (groups or []) if str(item).strip()
        }
        # 先查组织目录（id + 名称一次齐）；与 JWT groups 求交，保证授权仍以 token 为准。
        orgs = await _load_orgs(authorization)
        if orgs and jwt_groups:
            orgs = [org for org in orgs if org["group_id"] in jwt_groups]
        if orgs:
            candidate_groups = [(org["group_id"], org["group_name"]) for org in orgs]
            member_groups = {gid for gid, _ in candidate_groups}
        elif jwt_groups:
            candidate_groups = [(gid, gid) for gid in sorted(jwt_groups)]
            member_groups = jwt_groups
        else:
            candidate_groups = [(_NO_ORG_GROUP_ID, "无组织")]
            member_groups = set()

        admitted = await self._admitted_instance_ids(uid, member_groups)
        if not admitted:
            return [], 0

        name_by_jid = await self._instance_display_names(admitted)
        name_by_pair = await self._agent_definition_names()
        template_flags = await self._agent_template_flags()
        seen: set[tuple[str, str, str, str]] = set()
        out: list[dict[str, Any]] = []
        matched = 0
        for jid in sorted(admitted):
            rows = await self._h.list_records(
                _GRANT, {"jiuwenclaw_id": jid}, limit=_CAP, offset=0
            )
            cluster_name = name_by_jid.get(jid) or jid
            scope_index = (
                await build_service_scope_index(self._h, jid) if rows else []
            )
            for r in rows:
                if not bool(_g(r, "enabled", True)) or _grant_expired(_g(r, "expires_at")):
                    continue
                bot_id = str(_g(r, "resource_id") or "").strip()
                if not bot_id:
                    continue
                expr = _g(r, "match_expr")
                agent_template_id = str(_g(r, "ref_template_id") or "").strip()
                resource_name = str(_g(r, "resource_name") or "").strip()
                for group_id, group_name in candidate_groups:
                    if not evaluate_match_expr(
                        expr, user_id=uid, group_id=group_id, bot_id=""
                    ):
                        continue
                    key = (bot_id, group_id, uid, jid)
                    if key in seen:
                        continue
                    seen.add(key)
                    matched += 1
                    runtime_template_id = resolve_service_template_id(
                        scope_index,
                        user_id=uid,
                        group_id=group_id,
                        bot_id=bot_id,
                    )
                    identity = try_build_agent_identity(
                        runtime_template_id, agent_template_id
                    )
                    if identity is None:
                        continue
                    agent_id = identity[0]
                    configured_name = name_by_pair.get(
                        (agent_template_id, str(runtime_template_id or ""))
                    )
                    if not configured_name:
                        # 管理面未新增该组合：用户面不展示。
                        continue
                    agent_name = configured_name
                    out.append(
                        {
                            "bot_id": bot_id,
                            "group_id": group_id,
                            "user_id": uid,
                            "jiuwenclaw_id": jid,
                            "runtime_template_id": runtime_template_id,
                            "agent_template_id": agent_template_id,
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "resource_name": resource_name,
                            "allow_local_skill_upload": template_flags.get(
                                agent_template_id, False
                            ),
                            "group_name": group_name,
                            "jiuwenclaw_name": cluster_name,
                        }
                    )
        # 用户面下拉只展示 agent_name（默认 agent_ + agent_id，可由管理员改名），
        # 按其排序保证接口返回顺序与展示顺序一致。
        out.sort(
            key=lambda x: (
                str(x.get("agent_name") or ""),
                str(x.get("group_name") or ""),
                str(x.get("jiuwenclaw_name") or ""),
                str(x.get("bot_id") or ""),
                str(x.get("group_id") or ""),
                str(x.get("jiuwenclaw_id") or ""),
            )
        )
        return out, matched

    async def _agent_template_flags(self) -> dict[str, bool]:
        """Agent 配置模板的扩展开关；缺失或非法一律按 false 处理。"""
        rows = await self._h.list_records(
            _AGENT_TEMPLATE, {}, limit=_CAP, offset=0
        )
        flags: dict[str, bool] = {}
        for row in rows:
            template_id = str(_g(row, "template_id") or "").strip()
            if not template_id:
                continue
            data = _g(row, "data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = None
            flags[template_id] = bool(
                isinstance(data, dict)
                and data.get("upload_local_skill") is True
            )
        return flags

    async def _agent_definition_names(self) -> dict[tuple[str, str], str]:
        """Agent 模板的 ``(Agent 配置模板 id, 运行时模板 id)`` → 名称。"""
        rows = await self._h.list_records(
            _AGENT_DEFINITION, {}, limit=_CAP, offset=0
        )
        names: dict[tuple[str, str], str] = {}
        for row in rows:
            name = str(_g(row, "agent_name") or "").strip()
            if not name:
                continue
            key = (
                str(_g(row, "agent_config_template_id") or ""),
                str(_g(row, "runtime_template_id") or ""),
            )
            names[key] = name
        return names

    async def _instance_display_names(self, jids: set[str]) -> dict[str, str]:
        """``jiuwenclaw_id`` → ``jiuwenclaw_name``（缺省回退为 id）。"""
        if not jids:
            return {}
        rows = await self._h.list_records(_INSTANCE_INFO, {}, limit=_CAP, offset=0)
        out: dict[str, str] = {}
        for r in rows:
            jid = str(_g(r, "jiuwenclaw_id") or "").strip()
            if jid not in jids:
                continue
            name = str(_g(r, "jiuwenclaw_name") or "").strip() or jid
            out[jid] = name
        return out

    async def _admitted_instance_ids(
        self, user_id: str, member_groups: set[str]
    ) -> set[str]:
        allowed: set[str] = set()
        user_map = await self._instance_grants.list_instances_for(SUBJECT_USER, [user_id])
        allowed.update(user_map.get(user_id, []))
        if member_groups:
            org_map = await self._instance_grants.list_instances_for(
                SUBJECT_ORG, list(member_groups)
            )
            for ids in org_map.values():
                allowed.update(ids)
        result: set[str] = set()
        for jid in allowed:
            if await self._instance_grants.is_admitted(jid, user_id, member_groups):
                result.add(jid)
        return result

    async def user_can_access_instance(
        self,
        user_id: str,
        jiuwenclaw_id: str,
        groups: list[str] | None = None,
    ) -> bool:
        """当前用户是否对指定集群有 instance_grant 准入。"""
        jid = str(jiuwenclaw_id or "").strip()
        if not jid:
            return False
        member_groups = {
            str(item).strip() for item in (groups or []) if str(item).strip()
        }
        admitted = await self._admitted_instance_ids(
            str(user_id or "").strip(),
            member_groups,
        )
        return jid in admitted

    async def user_can_access_context(
        self,
        user_id: str,
        *,
        jiuwenclaw_id: str,
        group_id: str,
        bot_id: str,
        groups: list[str] | None = None,
        authorization: str | None = None,
    ) -> bool:
        """当前用户是否可对指定三元组 + 集群提交扩容（与 agent-contexts 同一准入口径）。"""
        uid = str(user_id or "").strip()
        jid = str(jiuwenclaw_id or "").strip()
        gid = str(group_id or "").strip()
        bid = str(bot_id or "").strip()
        if not uid or not jid or not bid:
            return False
        contexts = await self.list_accessible_contexts(
            uid, groups, authorization=authorization
        )
        for item in contexts:
            if (
                str(item.get("jiuwenclaw_id") or "") == jid
                and str(item.get("bot_id") or "") == bid
                and str(item.get("group_id") or "") == gid
            ):
                return True
        return False


__all__ = ("UserConsoleService",)
