"""按 Agent 资源模板引用关系，将各类模板定向下发到 Gateway。"""

from __future__ import annotations

import importlib
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.manager_config_push import (
    gateway_request,
    list_reachable_jiuwenclaw_ids,
)
from manager_server.infrastructure.template_ref import (
    normalize_template_ref,
    read_template_ref_from_row,
)
from manager_server.infrastructure.utils import utc_now
from manager_server.infrastructure.logger import get_logger
from manager_server.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from manager_server.models.instance_resource_models import (
    INSTANCE_AGENT_RESOURCE_TABLE_DEF,
)
from manager_server.models.template_models import (
    AGENT_TEMPLATE_TABLE_DEF,
    EMBEDDING_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MCP_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    SKILL_WHITELIST_TEMPLATE_TABLE_DEF,
)
from manager_server.schemas.template_slot_schemas import (
    EMBEDDING_MODEL_SLOT,
    EXTENSION_CONFIG_SLOT,
    MCP_SLOT,
    MODEL_TEMPLATE_SLOTS,
    SKILL_WHITELIST_SLOT,
)

logger = get_logger(__name__)

_LIST_ALL_CAP = 10_000
_OR_SPLIT_PATTERN = re.compile(r"\s+or\s+", flags=re.IGNORECASE)
_MAPPING_DIM_PATTERN = re.compile(r"^\$\{(user|group)::([^}]+)\}$", re.IGNORECASE)

_JID_TEMPLATE_REF_TABLE = JID_TEMPLATE_REF_TABLE_DEF.table_name


@dataclass(frozen=True)
class TemplateKindSpec:
    """一种可下发模板的元数据：WS config 段名、MDB 表名、``template_ref`` 槽位键。"""

    config_section: str
    table_name: str
    slot_keys: frozenset[str]


TEMPLATE_KIND_SPECS: dict[str, TemplateKindSpec] = {
    "model_templates": TemplateKindSpec(
        config_section="model_templates",
        table_name=MODEL_TEMPLATE_TABLE_DEF.table_name,
        slot_keys=MODEL_TEMPLATE_SLOTS,
    ),
    "embedding_templates": TemplateKindSpec(
        config_section="embedding_templates",
        table_name=EMBEDDING_TEMPLATE_TABLE_DEF.table_name,
        slot_keys=frozenset({EMBEDDING_MODEL_SLOT}),
    ),
    "skill_whitelist_templates": TemplateKindSpec(
        config_section="skill_whitelist_templates",
        table_name=SKILL_WHITELIST_TEMPLATE_TABLE_DEF.table_name,
        slot_keys=frozenset({SKILL_WHITELIST_SLOT}),
    ),
    "extension_config_templates": TemplateKindSpec(
        config_section="extension_config_templates",
        table_name=EXTENSION_CONFIG_TEMPLATE_TABLE_DEF.table_name,
        slot_keys=frozenset({EXTENSION_CONFIG_SLOT}),
    ),
    "mcp_templates": TemplateKindSpec(
        config_section="mcp_templates",
        table_name=MCP_TEMPLATE_TABLE_DEF.table_name,
        slot_keys=frozenset({MCP_SLOT}),
    ),
}

TEMPLATE_KIND_ORDER: tuple[str, ...] = tuple(TEMPLATE_KIND_SPECS.keys())

_TEMPLATE_HTTP_PATHS: dict[str, str] = {
    "model_templates": "/api/v1/model-templates",
    "embedding_templates": "/api/v1/embedding-templates",
    "extension_config_templates": "/api/v1/extension-config-templates",
    "skill_whitelist_templates": "/api/v1/skill-whitelist-templates",
    "mcp_templates": "/api/v1/mcp-templates",
}
_PUSH_DROP_KEYS = frozenset({"id", "created_at", "updated_at", "jiuwenclaw_id"})

_ROW_TO_OUT_MODULES: dict[str, str] = {
    "model_templates": "manager_server.core.template.model_template",
    "embedding_templates": "manager_server.core.template.embedding_template",
    "skill_whitelist_templates": "manager_server.core.template.skill_whitelist_template",
    "extension_config_templates": "manager_server.core.template.extension_config_template",
    "mcp_templates": "manager_server.core.template.mcp_template",
}

RowToSyncFn = Callable[[Any], dict[str, Any]]
RowToOutFn = Callable[[Any], Any]


def _normalize_kind(kind: str) -> str:
    normalized = str(kind or "").strip()
    if normalized not in TEMPLATE_KIND_SPECS:
        raise ValueError(
            f"unknown template kind {kind!r}; expected one of {sorted(TEMPLATE_KIND_SPECS)}"
        )
    return normalized


def _template_base_path(kind: str) -> str:
    return _TEMPLATE_HTTP_PATHS[_normalize_kind(kind)]


def _clean_template(template: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in template.items() if k not in _PUSH_DROP_KEYS}


async def _create_template_on_gateway(
    jiuwenclaw_id: str,
    kind: str,
    template: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    section = _normalize_kind(kind)
    return await gateway_request(
        jiuwenclaw_id,
        "POST",
        _template_base_path(section),
        _clean_template(template),
        **kwargs,
    )


async def _update_template_on_gateway(
    jiuwenclaw_id: str,
    kind: str,
    template_id: str,
    updates: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    section = _normalize_kind(kind)
    tid = str(template_id or "").strip()
    if not tid:
        raise ValueError("template_id is required")
    return await gateway_request(
        jiuwenclaw_id,
        "PATCH",
        f"{_template_base_path(section)}/{tid}",
        dict(updates),
        **kwargs,
    )


async def _delete_template_on_gateway(
    jiuwenclaw_id: str,
    kind: str,
    template_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    tid = str(template_id or "").strip()
    if not tid:
        raise ValueError("template_id is required")
    return await gateway_request(
        jiuwenclaw_id,
        "DELETE",
        f"{_template_base_path(kind)}/{tid}",
        {},
        **kwargs,
    )


async def _sync_templates_on_gateway(
    jiuwenclaw_id: str,
    kind: str,
    templates: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    section = _normalize_kind(kind)
    last: dict[str, Any] | None = None
    for idx, tmpl in enumerate(templates):
        if not isinstance(tmpl, dict):
            continue
        last = await _create_template_on_gateway(
            jiuwenclaw_id,
            section,
            tmpl,
            **kwargs,
        )
    return last or {
        "success_flag": True,
        "result": {"synced": 0},
        "transport": "http",
    }


def extract_literal_template_ids_from_ref(ref: str) -> set[str]:
    """从单条 ``template_ref`` 原始字符串提取字面 ``template_id``（含 ``or`` 右侧回退）。"""
    text = str(ref or "").strip()
    if not text:
        return set()
    out: set[str] = set()
    for part in _OR_SPLIT_PATTERN.split(text):
        part = part.strip()
        if not part:
            continue
        if _MAPPING_DIM_PATTERN.fullmatch(part) or part.startswith("${"):
            continue
        out.add(part)
    return out


def slot_template_pairs_from_template_ref(template_ref: Any) -> set[tuple[str, str]]:
    """单条 ``template_ref`` 贡献的 ``(slot, template_id)`` 集合（每条引用每对至多计一次）。"""
    refs = normalize_template_ref(template_ref) if template_ref else {}
    out: set[tuple[str, str]] = set()
    for slot, raw_list in refs.items():
        slot_name = str(slot).strip()
        if not slot_name:
            continue
        for raw in raw_list:
            for tid in extract_literal_template_ids_from_ref(str(raw)):
                out.add((slot_name, tid))
    return out


def extract_template_ids_from_template_ref(
    template_ref: Any,
    *,
    slot_keys: frozenset[str] | None = None,
) -> set[str]:
    """从 ``template_ref`` 对象提取指定槽位下可能引用的 ``template_id`` 集合。"""
    pairs = slot_template_pairs_from_template_ref(template_ref)
    if slot_keys is None:
        return {tid for _, tid in pairs}
    return {tid for slot, tid in pairs if slot in slot_keys}


async def _count_slot_pairs_from_agent_resources(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> Counter[tuple[str, str]]:
    """每个 ``resource_id`` 对其 ``agent_template.template_ref`` 贡献一组 slot 引用。"""
    counter: Counter[tuple[str, str]] = Counter()
    rows = await handler.list_records(
        INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    seen: set[tuple[str, str]] = set()
    for row in rows:
        template_id = str(getattr(row, "ref_template_id", "") or "").strip()
        resource_id = str(getattr(row, "resource_id", "") or "").strip()
        if not template_id or not resource_id:
            continue
        dedupe_key = (template_id, resource_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        tpl_row = await handler.get(
            AGENT_TEMPLATE_TABLE_DEF.table_name,
            {"template_id": template_id},
        )
        if tpl_row is None:
            continue
        counter.update(
            slot_template_pairs_from_template_ref(
                read_template_ref_from_row(tpl_row)
            )
        )
    return counter


async def _count_slot_pairs_for_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> Counter[tuple[str, str]]:
    return await _count_slot_pairs_from_agent_resources(handler, jiuwenclaw_id)


async def _slot_pairs_for_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    slot_keys: frozenset[str],
) -> set[tuple[str, str]]:
    counter = await _count_slot_pairs_for_gateway(handler, jiuwenclaw_id)
    return {pair for pair in counter if pair[0] in slot_keys}


async def collect_referenced_template_ids_for_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
    kind: str,
) -> set[str]:
    """汇总某 Gateway 在 Agent 资源模板引用中引用的某类模板 ``template_id``。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return set()
    spec = TEMPLATE_KIND_SPECS[_normalize_kind(kind)]
    pairs = await _slot_pairs_for_gateway(handler, jid, slot_keys=spec.slot_keys)
    return {tid for _, tid in pairs}


async def _resolve_template_ref_lookup(
    handler: DBHandler,
    template_id: str,
    kind: str,
) -> tuple[str, frozenset[str], bool] | None:
    """解析模板引用查询上下文：``(template_id, slot_keys, use_jid_template_ref_index)``。"""
    tid = str(template_id or "").strip()
    if not tid:
        return None
    spec = TEMPLATE_KIND_SPECS[_normalize_kind(kind)]
    indexed = (
        await handler.count_records(_JID_TEMPLATE_REF_TABLE, {"template_id": tid})
    ) > 0
    return tid, spec.slot_keys, indexed


async def _list_active_jid_template_ref_rows(
    handler: DBHandler,
    template_id: str,
    *,
    slot_keys: frozenset[str],
) -> list[Any]:
    """从 ``jid_template_ref`` 列出指定 ``template_id`` 的有效引用行。"""
    rows = await handler.list_records(
        _JID_TEMPLATE_REF_TABLE,
        {"template_id": template_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    matched: list[Any] = []
    for row in rows:
        slot = str(getattr(row, "slot", "") or "").strip()
        if slot not in slot_keys:
            continue
        if int(getattr(row, "ref_count", 0) or 0) <= 0:
            continue
        matched.append(row)
    return matched


async def _scan_agent_resource_references_for_template(
    handler: DBHandler,
    template_id: str,
    *,
    slot_keys: frozenset[str],
) -> tuple[set[str], int]:
    """扫描 Agent 资源引用，返回引用的 Gateway 集合与引用条数（索引未建立时回退）。"""
    jids: set[str] = set()
    count = 0
    resource_rows = await handler.list_records(
        INSTANCE_AGENT_RESOURCE_TABLE_DEF.table_name,
        {},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    seen: set[tuple[str, str, str]] = set()
    for row in resource_rows:
        ref_template_id = str(getattr(row, "ref_template_id", "") or "").strip()
        resource_id = str(getattr(row, "resource_id", "") or "").strip()
        jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
        if not ref_template_id or not resource_id or not jid:
            continue
        dedupe_key = (jid, ref_template_id, resource_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        tpl_row = await handler.get(
            AGENT_TEMPLATE_TABLE_DEF.table_name,
            {"template_id": ref_template_id},
        )
        if tpl_row is None:
            continue
        if template_id not in extract_template_ids_from_template_ref(
            read_template_ref_from_row(tpl_row),
            slot_keys=slot_keys,
        ):
            continue
        count += 1
        jids.add(jid)
    return jids, count


async def collect_referenced_jiuwenclaw_ids_for_template(
    handler: DBHandler,
    template_id: str,
    kind: str,
) -> set[str]:
    """查找在 ``jid_template_ref`` 或 Agent 资源中引用了指定 ``template_id`` 的全部 ``jiuwenclaw_id``。"""
    lookup = await _resolve_template_ref_lookup(handler, template_id, kind)
    if lookup is None:
        return set()
    tid, slot_keys, use_index = lookup
    if use_index:
        rows = await _list_active_jid_template_ref_rows(
            handler, tid, slot_keys=slot_keys
        )
        jids: set[str] = set()
        for row in rows:
            jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
            if jid:
                jids.add(jid)
        return jids
    jids, _ = await _scan_agent_resource_references_for_template(
        handler, tid, slot_keys=slot_keys
    )
    return jids


async def _reachable_gateway_jiuwenclaw_ids(handler: DBHandler) -> set[str]:
    """已上报 ``gateway_endpoint`` 的实例集合（HTTP 可达目标）。"""
    return set(await list_reachable_jiuwenclaw_ids(handler))


async def _referencing_reachable_jids(
    handler: DBHandler,
    kind: str,
    template_id: str,
) -> list[str]:
    """引用了该模板且已上报 endpoint 的 Gateway 列表。"""
    normalized = _normalize_kind(kind)
    tid = str(template_id or "").strip()
    if not tid:
        return []
    jids = await collect_referenced_jiuwenclaw_ids_for_template(
        handler, tid, normalized
    )
    reachable = await _reachable_gateway_jiuwenclaw_ids(handler)
    return sorted(jid for jid in jids if jid in reachable)


def _row_to_sync_payload(row: Any, *, row_to_out: RowToOutFn) -> dict[str, Any]:
    data = row_to_out(row).model_dump(mode="json")
    for key in ("id", "created_at", "updated_at"):
        data.pop(key, None)
    return data


def _resolve_row_to_sync(kind: str) -> RowToSyncFn:
    normalized = _normalize_kind(kind)
    module = importlib.import_module(_ROW_TO_OUT_MODULES[normalized])
    row_to_out = module.row_to_out
    return lambda row: _row_to_sync_payload(row, row_to_out=row_to_out)


async def _build_sync_payloads(
    handler: DBHandler,
    kind: str,
    template_ids: set[str],
) -> list[dict[str, Any]]:
    spec = TEMPLATE_KIND_SPECS[_normalize_kind(kind)]
    row_to_sync = _resolve_row_to_sync(kind)
    templates: list[dict[str, Any]] = []
    for template_id in sorted(template_ids):
        row = await handler.get(spec.table_name, {"template_id": template_id})
        if row is not None:
            templates.append(row_to_sync(row))
    return templates


async def sync_referenced_templates_to_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, dict[str, Any]]:
    """将某 Gateway 引用的全部模板类型 bulk 同步到 Config Receiver。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")

    results: dict[str, dict[str, Any]] = {}
    for kind in TEMPLATE_KIND_ORDER:
        normalized = _normalize_kind(kind)
        referenced_ids = await collect_referenced_template_ids_for_gateway(
            handler, jid, normalized
        )
        templates = await _build_sync_payloads(handler, normalized, referenced_ids)
        results[kind] = await _sync_templates_on_gateway(jid, normalized, templates)
    return results


async def update_template_on_referencing_gateways(
    handler: DBHandler,
    kind: str,
    template_id: str,
    updates: dict[str, Any],
) -> None:
    """向引用了该模板的可达 Gateway PATCH 更新。"""
    normalized = _normalize_kind(kind)
    tid = str(template_id or "").strip()
    if not tid:
        return
    reachable = set(await _referencing_reachable_jids(handler, normalized, tid))
    all_refs = await collect_referenced_jiuwenclaw_ids_for_template(
        handler, tid, normalized
    )
    for jid in sorted(all_refs):
        if jid not in reachable:
            logger.info(
                "[push_template] skip unreachable gateway jiuwenclaw_id=%s "
                "kind=%s action=update template_id=%s",
                jid,
                normalized,
                tid,
            )
            continue
        await _update_template_on_gateway(jid, normalized, tid, updates)


async def delete_template_on_referencing_gateways(
    handler: DBHandler,
    kind: str,
    template_id: str,
) -> None:
    """向引用了该模板的可达 Gateway DELETE 模板。"""
    normalized = _normalize_kind(kind)
    tid = str(template_id or "").strip()
    if not tid:
        return
    reachable = set(await _referencing_reachable_jids(handler, normalized, tid))
    all_refs = await collect_referenced_jiuwenclaw_ids_for_template(
        handler, tid, normalized
    )
    for jid in sorted(all_refs):
        if jid not in reachable:
            logger.info(
                "[push_template] skip unreachable gateway jiuwenclaw_id=%s "
                "kind=%s action=delete template_id=%s",
                jid,
                normalized,
                tid,
            )
            continue
        await _delete_template_on_gateway(jid, normalized, tid)


def _ref_pk(jiuwenclaw_id: str, slot: str, template_id: str) -> dict[str, str]:
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "slot": slot,
        "template_id": template_id,
    }


async def _adjust_ref_count(
    handler: DBHandler,
    jiuwenclaw_id: str,
    slot: str,
    template_id: str,
    delta: int,
    *,
    now: Any,
) -> None:
    if delta == 0:
        return
    pk = _ref_pk(jiuwenclaw_id, slot, template_id)
    row = await handler.get(_JID_TEMPLATE_REF_TABLE, pk)
    if delta > 0:
        if row is None:
            await handler.create(
                _JID_TEMPLATE_REF_TABLE,
                {
                    **pk,
                    "ref_count": delta,
                    "data": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return
        await handler.update(
            _JID_TEMPLATE_REF_TABLE,
            pk,
            {
                "ref_count": int(getattr(row, "ref_count", 0) or 0) + delta,
                "updated_at": now,
            },
        )
        return

    if row is None:
        return
    new_count = int(getattr(row, "ref_count", 0) or 0) + delta
    if new_count <= 0:
        await handler.delete(_JID_TEMPLATE_REF_TABLE, pk)
        return
    await handler.update(
        _JID_TEMPLATE_REF_TABLE,
        pk,
        {"ref_count": new_count, "updated_at": now},
    )


async def _snapshot_template_totals(
    handler: DBHandler,
    jiuwenclaw_id: str,
    template_ids: set[str],
) -> dict[str, int]:
    totals: dict[str, int] = dict.fromkeys(template_ids, 0)
    if not totals:
        return totals
    rows = await handler.list_records(
        _JID_TEMPLATE_REF_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    for row in rows:
        tid = str(getattr(row, "template_id", "") or "").strip()
        if tid not in totals:
            continue
        count = int(getattr(row, "ref_count", 0) or 0)
        if count > 0:
            totals[tid] += count
    return totals


async def _resolve_template_kind(handler: DBHandler, template_id: str) -> str | None:
    for kind in TEMPLATE_KIND_ORDER:
        spec = TEMPLATE_KIND_SPECS[kind]
        row = await handler.get(spec.table_name, {"template_id": template_id})
        if row is not None:
            return kind
    return None


async def _apply_slot_pair_delta(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    added: set[tuple[str, str]],
    removed: set[tuple[str, str]],
    skip_runtime_update: bool = False,
) -> None:
    """按合计引用数差异向 Gateway 增量 create / delete，成功后更新 ``jid_template_ref``。

    Gateway 推送置于 Manager 引用计数更新之前：若推送失败则引用计数不变，重试时仍会
    触发 create/delete，避免「Manager 已记账、Gateway 缺模板」的不一致。
    """
    _ = skip_runtime_update
    jid = str(jiuwenclaw_id or "").strip()
    if not jid or (not added and not removed):
        return

    affected_template_ids = {tid for _, tid in added | removed}
    before_totals = await _snapshot_template_totals(
        handler, jid, affected_template_ids
    )

    tid_delta: Counter[str] = Counter()
    for _, tid in added:
        tid_delta[tid] += 1
    for _, tid in removed:
        tid_delta[tid] -= 1

    for tid in sorted(affected_template_ids):
        before = before_totals[tid]
        after = before + tid_delta[tid]
        kind = await _resolve_template_kind(handler, tid)
        if kind is None:
            continue
        if before <= 0 < after:
            payloads = await _build_sync_payloads(handler, kind, {tid})
            if not payloads:
                continue
            await _create_template_on_gateway(jid, kind, payloads[0])
        elif before > 0 >= after:
            await _delete_template_on_gateway(jid, kind, tid)

    now = utc_now()
    for slot, tid in removed:
        await _adjust_ref_count(handler, jid, slot, tid, -1, now=now)
    for slot, tid in added:
        await _adjust_ref_count(handler, jid, slot, tid, 1, now=now)


async def sync_gateway_templates_after_template_ref_change(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    old_template_ref: Any,
    new_template_ref: Any,
    skip_runtime_update: bool = False,
) -> None:
    old_pairs = slot_template_pairs_from_template_ref(old_template_ref)
    new_pairs = slot_template_pairs_from_template_ref(new_template_ref)
    await _apply_slot_pair_delta(
        handler,
        jiuwenclaw_id,
        added=new_pairs - old_pairs,
        removed=old_pairs - new_pairs,
        skip_runtime_update=skip_runtime_update,
    )


async def rebuild_jid_template_ref_for_gateway(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> None:
    """从 Agent 资源模板引用重建某 Gateway 的 ``jid_template_ref`` 索引。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return

    existing = await handler.list_records(
        _JID_TEMPLATE_REF_TABLE,
        {"jiuwenclaw_id": jid},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    for row in existing:
        await handler.delete(
            _JID_TEMPLATE_REF_TABLE,
            _ref_pk(
                jid,
                str(getattr(row, "slot", "") or ""),
                str(getattr(row, "template_id", "") or ""),
            ),
        )

    counter = await _count_slot_pairs_for_gateway(handler, jid)
    now = utc_now()
    for (slot, template_id), ref_count in sorted(counter.items()):
        if ref_count <= 0:
            continue
        await handler.create(
            _JID_TEMPLATE_REF_TABLE,
            {
                "jiuwenclaw_id": jid,
                "slot": slot,
                "template_id": template_id,
                "ref_count": ref_count,
                "data": None,
                "created_at": now,
                "updated_at": now,
            },
        )


async def collect_jiuwenclaw_ids_referencing_template(
    handler: DBHandler,
    template_id: str,
    *,
    slot_keys: frozenset[str],
) -> set[str]:
    """从 ``jid_template_ref`` 查找引用指定 ``template_id`` 的 Gateway。"""
    tid = str(template_id or "").strip()
    if not tid:
        return set()
    rows = await _list_active_jid_template_ref_rows(
        handler, tid, slot_keys=slot_keys
    )
    jids: set[str] = set()
    for row in rows:
        jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
        if jid:
            jids.add(jid)
    return jids


async def count_config_effective_policy_references_for_template(
    handler: DBHandler,
    template_id: str,
    kind: str,
) -> int:
    """统计 Agent 资源模板引用对指定 ``template_id`` 的引用条数。"""
    lookup = await _resolve_template_ref_lookup(handler, template_id, kind)
    if lookup is None:
        return 0
    tid, slot_keys, use_index = lookup
    if use_index:
        rows = await _list_active_jid_template_ref_rows(
            handler, tid, slot_keys=slot_keys
        )
        return sum(int(getattr(row, "ref_count", 0) or 0) for row in rows)
    _, count = await _scan_agent_resource_references_for_template(
        handler, tid, slot_keys=slot_keys
    )
    return count


async def assert_template_deletable(
    handler: DBHandler,
    template_id: str,
    kind: str,
) -> None:
    """删除模板前校验：若仍被 Agent 资源模板引用则拒绝删除。"""
    ref_count = await count_config_effective_policy_references_for_template(
        handler,
        template_id,
        kind,
    )
    if ref_count > 0:
        raise ValueError(
            f"cannot delete template: {ref_count} template "
            "reference(s) exist, remove references from agent templates first"
        )


__all__ = (
    "assert_template_deletable",
    "count_config_effective_policy_references_for_template",
    "sync_referenced_templates_to_gateway",
    "update_template_on_referencing_gateways",
    "delete_template_on_referencing_gateways",
    "sync_gateway_templates_after_template_ref_change",
    "rebuild_jid_template_ref_for_gateway",
)
