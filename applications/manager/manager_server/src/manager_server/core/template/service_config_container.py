"""service_config_container：平台全局容器规格目录。

SoT 为表行；API/前端仍可在 ``data.config_sync.containers`` 携带 wire 形态，
create/update 时抽出落表并从 data 剥离；get/list Out 再投影回 data 供编辑页。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.infrastructure.utils import utc_now
from manager_server.models.template_models import SERVICE_CONFIG_CONTAINER_TABLE_DEF

_TABLE = SERVICE_CONFIG_CONTAINER_TABLE_DEF.table_name

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
    """从 data 去掉 config_sync.containers，保留 scopes 等元数据。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    out = dict(data)
    sync = out.get("config_sync")
    if not isinstance(sync, dict):
        return out or None
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
    """DB 行 → config_sync wire 容器。"""
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
        wire["data"] = data
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
    """按 container_id UPSERT wire 容器到表。"""
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


async def hydrate_data_with_containers(
    handler: DBHandler, template_row: Any
) -> dict[str, Any] | None:
    """Out 投影：把表内容器填回 data.config_sync.containers，便于前端编辑。"""
    data = _g(template_row, "data")
    base: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    wires = await load_wires_for_template(handler, template_row)
    if not wires:
        return base or None
    sync = base.get("config_sync") if isinstance(base.get("config_sync"), dict) else {}
    sync = dict(sync)
    sync["containers"] = wires
    base["config_sync"] = sync
    return base


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
