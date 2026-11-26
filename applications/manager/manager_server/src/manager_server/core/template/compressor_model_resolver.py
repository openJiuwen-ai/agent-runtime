"""React 模板压缩器模型解析：把 body 内联的 ``model_template_id`` 转成压缩器所需的内联
``model`` / ``model_client`` 配置。

压缩器（dialogue / current_round / round_level）继承 ``PrefixCompactProcessorConfig``，
需要 ``model``（ModelRequestConfig）与 ``model_client``（ModelClientConfig）两个内联子对象；
若二者缺省，运行时 ``ContextProcessorRail`` 会回填主 agent 模型（见 agent-core
``context_processor_rail.py`` 的 ``_merge_processors``）。

本模块只做「model_template 行 → 内联配置」的纯映射与 DB 查询，不 import 推送模块，
避免与 ``push_template_to_gateway`` 形成循环依赖。
"""

from __future__ import annotations

import copy
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.models.template_models import (
    MODEL_TEMPLATE_TABLE_DEF,
    REACT_TEMPLATE_TABLE_DEF,
)

# 引用扫描分页大小：react_template 行数可能超过单页上限，必须按页遍历，
# 与 push_template_to_gateway.count_agent_template_reference_counts 的翻页契约一致。
_REACT_SCAN_PAGE_SIZE = 500

# 调用模型的三个压缩器子配置键（其余子配置不调用模型）。
COMPRESSOR_CONFIG_KEYS: tuple[str, ...] = (
    "dialogue_compressor_config",
    "current_round_compressor_config",
    "round_level_compressor_config",
)


def _model_template_table() -> str:
    return MODEL_TEMPLATE_TABLE_DEF.table_name


def _react_template_table() -> str:
    return REACT_TEMPLATE_TABLE_DEF.table_name


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_compressor_model(row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """把一条 ``model_template`` 行映射成压缩器的 (``model``, ``model_client``) 内联配置。

    参照 agent-core ``llm_controller._get_model``（``core/application/llm_agent/
    llm_controller.py``）的逐字段映射：
    - ``model`` 对应 ``ModelRequestConfig``：``model`` 用 ``model_id``，其余键由
      ``parameters``（json dict，temperature/top_p/max_tokens 等）透传。
    - ``model_client`` 对应 ``ModelClientConfig``：``client_provider`` 原样透传
      （运行时 Config 自动归一化 provider 别名），``api_key/api_base/timeout/
      verify_ssl/max_retries/custom_headers`` 分别取自模型模板对应列。
    """
    parameters = _as_dict(getattr(row, "parameters", None))
    # parameters 为自由透传 JSON，可能残留 "model" 同名键（如跨厂商配置导入）；
    # model_id 最后写入以保证优先，避免内联 model 与模板 model_id/api_base 不匹配。
    model: dict[str, Any] = dict(parameters)
    model["model"] = getattr(row, "model_id", None) or ""

    model_client: dict[str, Any] = {
        "client_provider": getattr(row, "model_provider", None) or "",
        "api_key": getattr(row, "api_key", None) or "",
        "api_base": getattr(row, "api_base", None) or "",
        "timeout": getattr(row, "timeout", None) if getattr(row, "timeout", None) is not None else 360.0,
        # verify_ssl 忠实透传模型模板列值（列 nullable=False、default=False，未显式配置即为 False）。
        "verify_ssl": bool(getattr(row, "verify_ssl", False)),
    }
    retry_count = getattr(row, "retry_count", None)
    if retry_count is not None:
        model_client["max_retries"] = retry_count

    data = _as_dict(getattr(row, "data", None))
    headers = data.get("headers")
    if isinstance(headers, dict) and headers:
        model_client["custom_headers"] = headers

    return model, model_client


def extract_react_model_template_ids(body: dict[str, Any]) -> set[str]:
    """扫描 body 中三个压缩器子配置的 ``model_template_id`` 引用集合。"""
    out: set[str] = set()
    context_engine = body.get("context_engine_config")
    if not isinstance(context_engine, dict):
        return out
    for key in COMPRESSOR_CONFIG_KEYS:
        cfg = context_engine.get(key)
        if not isinstance(cfg, dict):
            continue
        template_id = cfg.get("model_template_id")
        if isinstance(template_id, str) and template_id.strip():
            out.add(template_id.strip())
    return out


async def resolve_react_body_models(handler: DBHandler, body: dict[str, Any]) -> dict[str, Any]:
    """返回解析后的 body（深拷贝）：每个含 ``model_template_id`` 的压缩器注入
    ``model`` + ``model_client`` 并移除 ``model_template_id``。

    引用的 ``template_id`` 不存在时抛 ``ValueError``。
    """
    resolved = copy.deepcopy(body)
    context_engine = resolved.get("context_engine_config")
    if not isinstance(context_engine, dict):
        return resolved
    for key in COMPRESSOR_CONFIG_KEYS:
        cfg = context_engine.get(key)
        if not isinstance(cfg, dict):
            continue
        template_id = cfg.get("model_template_id")
        if not isinstance(template_id, str) or not template_id.strip():
            continue
        row = await handler.get(_model_template_table(), {"template_id": template_id.strip()})
        if row is None:
            raise ValueError(f"model template {template_id!r} referenced by {key} not found")
        model, model_client = build_compressor_model(row)
        cfg["model"] = model
        cfg["model_client"] = model_client
        cfg.pop("model_template_id", None)
    return resolved


async def validate_react_model_refs(handler: DBHandler, body: dict[str, Any]) -> None:
    """create/update 校验：body 内引用的 ``model_template_id`` 必须存在。"""
    for template_id in extract_react_model_template_ids(body or {}):
        row = await handler.get(_model_template_table(), {"template_id": template_id})
        if row is None:
            raise ValueError(f"model template {template_id!r} referenced by react body not found")


async def find_react_templates_referencing_model(
    handler: DBHandler, model_template_id: str
) -> list[str]:
    """返回 body 中引用了指定 ``model_template_id`` 的全部 react 模板 ``template_id``。

    按页遍历全量 react_template：行数超单页上限时单次查询会静默低估引用数，
    导致 model_template 删除保护失效。
    """
    tid = str(model_template_id or "").strip()
    if not tid:
        return []
    out: list[str] = []
    offset = 0
    while True:
        rows = await handler.list_records(
            _react_template_table(),
            {},
            limit=_REACT_SCAN_PAGE_SIZE,
            offset=offset,
            order_by=[("id", False)],
        )
        for row in rows:
            data = getattr(row, "data", None)
            if not isinstance(data, dict):
                continue
            if tid in extract_react_model_template_ids(data):
                react_tid = str(getattr(row, "template_id", "") or "").strip()
                if react_tid:
                    out.append(react_tid)
        if len(rows) < _REACT_SCAN_PAGE_SIZE:
            return out
        offset += len(rows)


async def count_react_model_references(handler: DBHandler, model_template_id: str) -> int:
    """引用指定 ``model_template_id`` 的 react 模板数（用于删除保护）。"""
    return len(await find_react_templates_referencing_model(handler, model_template_id))


async def count_react_model_reference_counts(handler: DBHandler) -> dict[str, int]:
    """统计每个 ``model_template_id`` 被多少个 react 模板引用（与删除保护同源）。

    同一 react 模板对同一模型至多计一次（多个压缩器引用同一模型只算一次）。
    """
    counts: dict[str, int] = {}
    offset = 0
    while True:
        rows = await handler.list_records(
            _react_template_table(),
            {},
            limit=_REACT_SCAN_PAGE_SIZE,
            offset=offset,
            order_by=[("id", False)],
        )
        for row in rows:
            data = getattr(row, "data", None)
            if not isinstance(data, dict):
                continue
            for model_id in extract_react_model_template_ids(data):
                counts[model_id] = counts.get(model_id, 0) + 1
        if len(rows) < _REACT_SCAN_PAGE_SIZE:
            return counts
        offset += len(rows)
