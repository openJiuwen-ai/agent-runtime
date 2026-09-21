"""service_config_container：平台全局容器规格目录（容器模板）。

SoT 为表行；运行时模板经 ``main_container_id`` / ``sidecar_container_ids`` 绑定。
表结构仅含容器规格列（container_id/name/image/…/data/时间戳）：
模板元数据（template_name/description/enabled）收编进 ``data._meta``，
API 主键 ``template_id`` 由唯一列 ``container_id`` 兼任（不做 uuid，避免加列）。
历史兼容：API/前端仍可在 ``data.config_sync.containers`` 携带 wire 形态，
模板 create/update 时抽出落表并从 data 剥离。
容器模板自身提供 REST CRUD（/v1/container-templates）。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.common import resolve_order_by
from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.template_models import (
    SERVICE_CONFIG_CONTAINER_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
)
from manager_server.schemas.template_schemas import (
    ServiceConfigContainerCreateBody,
    ServiceConfigContainerListQuery,
    ServiceConfigContainerOut,
    ServiceConfigContainerUpdateBody,
)

_TABLE = SERVICE_CONFIG_CONTAINER_TABLE_DEF.table_name
_SVC_TPL_TABLE = SERVICE_CONFIG_TEMPLATE_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "container_id",
    "updated_at",
})

# 模板元数据在 data JSON 列里的存放键（表结构无对应列，收编进 data）
_META_KEY = "_meta"

# DB JSON 列 ↔ wire camelCase 键
_JSON_WIRE_KEYS: tuple[tuple[str, str], ...] = (
    ("ports", "ports"),
    ("env", "env"),
    ("env_from", "envFrom"),
    ("resources", "resources"),
    ("volume_mounts", "volumeMounts"),
    ("security_context", "securityContext"),
    ("command", "command"),
    ("args", "args"),
    ("readiness_probe", "readinessProbe"),
)


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _meta_of(row: Any) -> dict[str, Any]:
    """读 data._meta 模板元数据（template_name/description/enabled）。"""
    data = _g(row, "data")
    if not isinstance(data, dict):
        return {}
    meta = data.get(_META_KEY)
    return meta if isinstance(meta, dict) else {}


def _merge_meta(data: Any, meta: dict[str, Any]) -> dict[str, Any] | None:
    """把模板元数据合并进 data 字典（保留 config_sync 等其他键）。"""
    base = dict(data) if isinstance(data, dict) else {}
    base[_META_KEY] = meta
    return base or None


def extract_wire_containers(data: Any) -> list[dict[str, Any]]:
    """从 data.config_sync.containers 取出 wire 列表。"""
    if not isinstance(data, dict):
        return []
    sync = data.get("config_sync")
    if not isinstance(sync, dict):
        return []
    containers = sync.get("containers")
    if not isinstance(containers, list):
        return []
    return [c for c in containers if isinstance(c, dict) and c.get("container_id")]


def strip_containers_from_data(data: Any) -> dict[str, Any] | None:
    """从 data 去掉 config_sync.containers，保留 scopes/_meta 等元数据。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    out = dict(data)
    sync = out.get("config_sync")
    if isinstance(sync, dict):
        sync_out = {k: v for k, v in sync.items() if k != "containers"}
        if sync_out:
            out["config_sync"] = sync_out
        else:
            out.pop("config_sync", None)
    return out or None


def wire_to_row(wire: dict[str, Any]) -> dict[str, Any]:
    """config_sync wire 容器 → DB 行字段（不含时间戳）。"""
    cid = str(wire.get("container_id") or "").strip()
    if not cid:
        raise ValueError("container_id is required")
    pull = wire.get("imagePullPolicy") or wire.get("image_pull_policy") or "IfNotPresent"
    row: dict[str, Any] = {
        "container_id": cid,
        "name": str(wire.get("name") or "agent"),
        "image": str(wire.get("image") or ""),
        "image_pull_policy": str(pull),
    }
    for db_key, wire_key in _JSON_WIRE_KEYS:
        if wire_key in wire:
            row[db_key] = wire.get(wire_key)
        elif db_key in wire:
            row[db_key] = wire.get(db_key)
        else:
            row[db_key] = None
    # 扩展字段：wire 可选带 data；未带则写 None（与模板/资源表 triad 对齐）
    row["data"] = wire.get("data") if isinstance(wire.get("data"), dict) else None
    return row


def row_to_wire(row: Any) -> dict[str, Any]:
    """DB 行 → config_sync wire 容器（不带 _meta，运行时无需模板元数据）。"""
    wire: dict[str, Any] = {
        "container_id": str(_g(row, "container_id") or ""),
        "name": str(_g(row, "name") or "agent"),
        "image": str(_g(row, "image") or ""),
        "imagePullPolicy": str(_g(row, "image_pull_policy") or "IfNotPresent"),
    }
    for db_key, wire_key in _JSON_WIRE_KEYS:
        value = _g(row, db_key)
        if value is not None:
            wire[wire_key] = value
    data = _g(row, "data")
    if isinstance(data, dict):
        wire_data = {k: v for k, v in data.items() if k != _META_KEY}
        if wire_data:
            wire["data"] = wire_data
    return wire


def referenced_container_ids(row: Any) -> list[str]:
    """模板行引用的 container_id 列表（主 + sidecar，去重保序）。"""
    ids: list[str] = []
    main = _g(row, "main_container_id")
    if isinstance(main, str) and main.strip():
        ids.append(main.strip())
    side = _g(row, "sidecar_container_ids")
    if isinstance(side, list):
        for item in side:
            if isinstance(item, str) and item.strip() and item.strip() not in ids:
                ids.append(item.strip())
    return ids


async def upsert_wire_containers(
    handler: DBHandler, wires: list[dict[str, Any]]
) -> None:
    """按 container_id UPSERT wire 容器到表（不覆盖既有 _meta 元数据）。"""
    now = utc_now()
    for wire in wires:
        row = wire_to_row(wire)
        existing = await handler.get(_TABLE, {"container_id": row["container_id"]})
        if existing is None:
            payload = dict(row)
            payload["created_at"] = now
            payload["updated_at"] = now
            await handler.create(_TABLE, payload)
        else:
            payload = dict(row)
            # 保留目录条目的模板元数据（data._meta）不被 wire 覆盖
            existing_meta = _meta_of(existing)
            if existing_meta:
                payload["data"] = _merge_meta(payload["data"], existing_meta)
            payload["updated_at"] = now
            await handler.update(_TABLE, {"container_id": row["container_id"]}, payload)


async def load_wires_by_ids(
    handler: DBHandler, container_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """按 id 批量加载 wire 容器；缺失的 id 不出现在结果中。"""
    out: dict[str, dict[str, Any]] = {}
    for cid in container_ids:
        if not cid or cid in out:
            continue
        row = await handler.get(_TABLE, {"container_id": cid})
        if row is None:
            continue
        out[cid] = row_to_wire(row)
    return out


async def load_wires_for_template(
    handler: DBHandler, template_row: Any
) -> list[dict[str, Any]]:
    """加载模板引用的容器 wire；表空时回退 data.config_sync.containers（存量过渡）。"""
    ids = referenced_container_ids(template_row)
    by_id = await load_wires_by_ids(handler, ids)
    if by_id:
        return [by_id[cid] for cid in ids if cid in by_id]
    # 存量：尚未迁表，仍可读 JSON
    return extract_wire_containers(_g(template_row, "data"))


async def main_image_from_table(
    handler: DBHandler, template_row: Any
) -> str | None:
    main = _g(template_row, "main_container_id")
    if not isinstance(main, str) or not main.strip():
        return None
    row = await handler.get(_TABLE, {"container_id": main.strip()})
    if row is None:
        # 存量 fallback
        for c in extract_wire_containers(_g(template_row, "data")):
            if str(c.get("container_id") or "") == main.strip():
                text = str(c.get("image") or "").strip()
                return text or None
        return None
    text = str(_g(row, "image") or "").strip()
    return text or None


async def load_row_by_container_id(
    handler: DBHandler, container_id: str
) -> Any | None:
    """按业务键 container_id 查容器模板行；无值返回 None。"""
    cid = str(container_id or "").strip()
    if not cid:
        return None
    return await handler.get(_TABLE, {"container_id": cid})


async def count_referencing_templates(handler: DBHandler, container_id: str) -> int:
    """统计绑定该容器模板的运行时模板数（main 或 sidecar，去重）。"""
    cid = str(container_id or "").strip()
    if not cid:
        return 0
    rows = await handler.list_records(
        _SVC_TPL_TABLE, {}, limit=_LIST_ALL_CAP, offset=0
    )
    count = 0
    for row in rows:
        if cid in referenced_container_ids(row):
            count += 1
    return count


async def list_referencing_template_ids(
    handler: DBHandler, container_id: str
) -> list[str]:
    """列出绑定该容器模板的运行时模板 template_id（main 或 sidecar，去重）。"""
    cid = str(container_id or "").strip()
    if not cid:
        return []
    rows = await handler.list_records(
        _SVC_TPL_TABLE, {}, limit=_LIST_ALL_CAP, offset=0
    )
    out: list[str] = []
    for row in rows:
        if cid in referenced_container_ids(row):
            out.append(str(_g(row, "template_id") or ""))
    return [tid for tid in out if tid]


def _container_row_to_out(row: Any, *, reference_count: int = 0) -> ServiceConfigContainerOut:
    meta = _meta_of(row)
    return ServiceConfigContainerOut(
        id=_g(row, "id", 0),
        # API 主键与 container_id 同值（表结构无独立 template_id 列）
        template_id=str(_g(row, "container_id") or ""),
        template_name=str(meta.get("template_name") or ""),
        description=meta.get("description"),
        container_id=str(_g(row, "container_id") or ""),
        name=str(_g(row, "name") or "agent"),
        image=str(_g(row, "image") or ""),
        image_pull_policy=str(_g(row, "image_pull_policy") or "IfNotPresent"),
        ports=_g(row, "ports") if isinstance(_g(row, "ports"), list) else None,
        env=_g(row, "env") if isinstance(_g(row, "env"), list) else None,
        env_from=_g(row, "env_from") if isinstance(_g(row, "env_from"), list) else None,
        resources=_g(row, "resources") if isinstance(_g(row, "resources"), dict) else None,
        volume_mounts=(
            _g(row, "volume_mounts") if isinstance(_g(row, "volume_mounts"), list) else None
        ),
        security_context=(
            _g(row, "security_context") if isinstance(_g(row, "security_context"), dict) else None
        ),
        command=_g(row, "command") if isinstance(_g(row, "command"), list) else None,
        args=_g(row, "args") if isinstance(_g(row, "args"), list) else None,
        readiness_probe=(
            _g(row, "readiness_probe") if isinstance(_g(row, "readiness_probe"), dict) else None
        ),
        reference_count=reference_count,
        enabled=bool(meta.get("enabled", True)),
        data=_g(row, "data") if isinstance(_g(row, "data"), dict) else None,
        created_at=iso_datetime(_g(row, "created_at")),
        updated_at=iso_datetime(_g(row, "updated_at")),
    )


def _matches_search(row: Any, query: str) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    meta = _meta_of(row)
    fields = [
        str(_g(row, "container_id", "") or ""),
        str(meta.get("template_name") or ""),
        str(meta.get("description") or ""),
        str(_g(row, "image", "") or ""),
    ]
    return any(needle in field.lower() for field in fields)


class ServiceConfigContainerService:
    """容器模板 CRUD（API 主键 template_id = 唯一列 container_id）。"""

    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _out(self, row: Any) -> ServiceConfigContainerOut:
        ref_count = await count_referencing_templates(
            self._handler, str(_g(row, "container_id") or "")
        )
        return _container_row_to_out(row, reference_count=ref_count)

    async def create(self, body: ServiceConfigContainerCreateBody) -> ServiceConfigContainerOut:
        cid = body.container_id.strip()
        if await self._handler.get(_TABLE, {"container_id": cid}) is not None:
            raise ValueError(f"container_id already exists: {cid}")
        now = utc_now()
        meta = {
            "template_name": body.template_name,
            "description": body.description,
            "enabled": bool(body.enabled),
        }
        payload: dict[str, Any] = {
            "container_id": cid,
            "name": body.name or "agent",
            "image": body.image or "",
            "image_pull_policy": body.image_pull_policy or "IfNotPresent",
            "ports": body.ports,
            "env": body.env,
            "env_from": body.env_from,
            "resources": body.resources,
            "volume_mounts": body.volume_mounts,
            "security_context": body.security_context,
            "command": body.command,
            "args": body.args,
            "readiness_probe": body.readiness_probe,
            "data": _merge_meta(body.data, meta),
            "created_at": now,
            "updated_at": now,
        }
        created = await self._handler.create(_TABLE, payload)
        return await self._out(created)

    async def get(self, template_id: str) -> ServiceConfigContainerOut | None:
        row = await self._handler.get(_TABLE, {"container_id": template_id})
        if row is None:
            return None
        return await self._out(row)

    async def list_containers(
        self, query: ServiceConfigContainerListQuery
    ) -> dict[str, Any]:
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        # enabled/template_name 存在 data._meta JSON 里，DB 层无法过滤/排序，
        # 统一拉全后内存筛选排序（存量目录量级小，_LIST_ALL_CAP 兜底）
        order_by = resolve_order_by(
            query.sort_by, query.sort_order, allowed_sort_fields=_ALLOWED_SORT_FIELDS
        )
        search_query = (query.search or "").strip()
        rows = await self._handler.list_records(
            _TABLE, {}, limit=_LIST_ALL_CAP, offset=0, order_by=order_by
        )

        def _visible(r: Any) -> bool:
            if query.enabled is not None and bool(_meta_of(r).get("enabled", True)) != query.enabled:
                return False
            return _matches_search(r, search_query) if search_query else True

        if (query.sort_by or "").strip() == "template_name":
            is_desc = (query.sort_order or "").strip().lower() == "desc"
            rows = sorted(
                rows,
                key=lambda r: str(_meta_of(r).get("template_name") or ""),
                reverse=is_desc,
            )

        items = [
            (await self._out(r)).model_dump(mode="json")
            for r in rows
            if _visible(r)
        ]
        total = len(items)
        offset = (page - 1) * page_size
        return {
            "items": items[offset:offset + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self, template_id: str, body: ServiceConfigContainerUpdateBody
    ) -> ServiceConfigContainerOut | None:
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            row = await self._handler.get(_TABLE, {"container_id": template_id})
            return await self._out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, {"container_id": template_id})
        if existing is None:
            return None

        old_cid = str(_g(existing, "container_id") or "")
        new_cid = updates.get("container_id")
        new_cid_is_rename = False
        if isinstance(new_cid, str) and new_cid.strip() and new_cid.strip() != old_cid:
            # 改名会静默打断既有绑定（运行时模板按 container_id 引用），直接拒绝
            ref_count = await count_referencing_templates(self._handler, old_cid)
            if ref_count > 0:
                raise ValueError(
                    f"cannot rename container_id: bound by {ref_count} runtime "
                    "template(s), unbind them first"
                )
            if await self._handler.get(_TABLE, {"container_id": new_cid.strip()}):
                raise ValueError(f"container_id already exists: {new_cid.strip()}")
            new_cid_is_rename = True

        # 元数据字段与规格字段分开处理：前者写 data._meta，后者直接写列
        meta_updates = {
            key: updates.pop(key)
            for key in ("template_name", "description", "enabled")
            if key in updates
        }
        payload: dict[str, Any] = dict(updates)
        if meta_updates:
            meta = _meta_of(existing)
            meta.update(meta_updates)
            payload["data"] = _merge_meta(_g(existing, "data"), meta)
        payload["updated_at"] = utc_now()

        row = await self._handler.update(
            _TABLE, {"container_id": old_cid}, payload
        )
        if row is None:
            return None
        # rename 场景：update 的回读按旧 container_id 过滤会落空，改读新键
        if new_cid_is_rename:
            row = await self._handler.get(
                _TABLE, {"container_id": str(payload["container_id"])}
            )
            if row is None:
                return None

        # 容器规格变更后重推：对所有绑定了该容器的运行时模板（可能多个模板
        # 共享同一实例），按实例去重后走与模板更新相同的 config_sync 重投影
        # （落库之后再推）。
        from manager_server.core.template.push_template_to_runtime import (
            collect_jiuwenclaw_ids_for_service_template,
            update_service_template_on_referencing_jids,
        )

        jids: set[str] = set()
        for tid in await list_referencing_template_ids(
            self._handler, str(_g(row, "container_id") or "")
        ):
            jids |= await collect_jiuwenclaw_ids_for_service_template(self._handler, tid)
        await update_service_template_on_referencing_jids(self._handler, jids)
        return await self._out(row)

    async def delete(self, template_id: str) -> bool:
        row = await self._handler.get(_TABLE, {"container_id": template_id})
        if row is None:
            return False
        cid = str(_g(row, "container_id") or "").strip()
        # 删除前校验：仍被运行时模板绑定则拒绝（主或 sidecar 均算）
        ref_count = await count_referencing_templates(self._handler, cid)
        if ref_count > 0:
            raise ValueError(
                f"cannot delete container template: bound by {ref_count} runtime "
                "template(s), unbind them first"
            )
        return await self._handler.delete(_TABLE, {"container_id": cid})


async def bound_container_briefs(
    handler: DBHandler, template_row: Any
) -> list[dict[str, Any]]:
    """运行时模板 Out 投影：绑定容器模板摘要（main 在前，sidecar 随后）。

    仅返回能在容器模板目录解析到的引用；存量 data.config_sync 内联形态不投影。
    """
    ids = referenced_container_ids(template_row)
    if not ids:
        return []
    briefs: list[dict[str, Any]] = []
    for cid in ids:
        row = await load_row_by_container_id(handler, cid)
        if row is None:
            continue
        meta = _meta_of(row)
        briefs.append(
            {
                "container_id": cid,
                "template_id": str(_g(row, "container_id") or ""),
                "template_name": str(meta.get("template_name") or ""),
                "image": str(_g(row, "image") or ""),
            }
        )
    return briefs
