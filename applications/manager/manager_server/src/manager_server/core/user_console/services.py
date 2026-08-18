"""用户控制台业务逻辑：当前用户可见 Agent。

instance_grant 在 core/instance_access；agent_template 在 core/template；
instance_agent_resource 在 core/instance_resource。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.instance_access import InstanceGrantService
from manager_server.core.instance_resource import grant_expired
from manager_server.core.template.agent_template import agent_template_out
from manager_server.infrastructure.match_expr import evaluate_match_expr
from manager_server.models.instance_resource_models import INSTANCE_AGENT_RESOURCE_TABLE_DEF

_GRANT = INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name
_AGENT_TPL = "agent_template"
_CAP = 100_000


def _g(row: Any, key: str, default: Any = None) -> Any:
    return getattr(row, key, default)


class UserConsoleService:
    """某实例 + 某组织上下文下,当前用户可见的 Agent。groups 由 JWT claims 传入(不查身份库)。"""

    def __init__(self, handler: DBHandler) -> None:
        self._h = handler
        self._instance_grants = InstanceGrantService(handler)

    async def list_visible_agents(
        self, user_id: str, group_id: str, groups: list[str] | None = None,
        jiuwenclaw_id: str = "",
        is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        if not jiuwenclaw_id:
            return []
        member_groups = set(groups or [])
        if not is_admin and not await self._instance_grants.is_admitted(
            jiuwenclaw_id, user_id, member_groups
        ):
            return []
        rows = await self._h.list_records(
            _GRANT, {"jiuwenclaw_id": jiuwenclaw_id}, limit=_CAP, offset=0
        )
        chosen: dict[str, str] = {}
        for r in rows:
            if not bool(_g(r, "enabled", True)) or grant_expired(_g(r, "expires_at")):
                continue
            resource_id = str(_g(r, "resource_id") or "")
            tid = str(_g(r, "ref_template_id") or "")
            if not resource_id or not tid or resource_id in chosen:
                continue
            if is_admin or evaluate_match_expr(
                _g(r, "match_expr"), user_id=user_id, group_id=group_id, bot_id=""
            ):
                chosen[resource_id] = tid
        out: list[dict[str, Any]] = []
        for resource_id, tid in chosen.items():
            b = await self._h.get(_AGENT_TPL, {"template_id": tid})
            if b is None or not bool(_g(b, "enabled", True)):
                continue
            item = agent_template_out(b)
            item["resource_id"] = resource_id
            item["ref_template_id"] = tid
            out.append(item)
        out.sort(key=lambda x: str(x.get("template_name") or ""))
        return out


__all__ = ("UserConsoleService",)
