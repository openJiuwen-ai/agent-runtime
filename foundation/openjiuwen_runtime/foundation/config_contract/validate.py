# coding: utf-8
"""三段式 config_sync 载荷的聚合校验(Manager 保存前预检用)。

与 Runtime ``ConfigStore._parse_payload`` 使用**同一套** foundation 解析函数
(containers / container_spec / template_spec),区别只是这里聚合全部错误而不是
遇错即抛,便于 Manager 一次性把问题反馈给页面(issue #203)。

scopes 段由 Runtime 自己校验(Manager 投影出的 scope 只引用已落库模板),
这里只校验最关键的 containers/templates 组合。
"""

from __future__ import annotations

from typing import Any

from .container_spec import parse_container_spec
from .containers import MAIN_ROLE, SIDECAR_ROLE
from .errors import ConfigContractError, is_contract_error
from .template_spec import template_split_kwargs


def _error_message(exc: BaseException) -> str:
    return str(exc)


def validate_service_config_payload(payload: Any) -> list[dict[str, str]]:
    """校验 ``{containers, templates, scopes}`` 载荷,返回错误列表。

    空列表 = 通过;每项形如 ``{"path": "templates[0].volumes", "message": ...}``。
    """
    errors: list[dict[str, str]] = []

    def _add(path: str, exc: BaseException) -> None:
        errors.append({"path": path, "message": _error_message(exc)})

    if not isinstance(payload, dict):
        return [{"path": "", "message": "payload must be an object"}]
    if "kind" in payload or "op" in payload:
        return [{
            "path": "",
            "message": "legacy kind/op payload no longer supported; send "
                       "{containers: [...], templates: [...], scopes: [...]}",
        }]
    if "containers" not in payload:
        return [{
            "path": "",
            "message": "payload requires the three-part contract "
                       "{containers, templates, scopes}",
        }]
    templates_raw = payload.get("templates")
    scopes_raw = payload.get("scopes")
    containers_raw = payload.get("containers")
    if not isinstance(templates_raw, list) or not isinstance(scopes_raw, list):
        return [{"path": "", "message": "templates and scopes must both be lists"}]
    if not isinstance(containers_raw, list):
        return [{"path": "", "message": "containers must be a list"}]

    # ---- 阶段 1:模板引用(与 _parse_payload 同口径)
    main_refs: dict[str, str] = {}
    sidecar_refs: dict[str, list[str]] = {}
    dropped: set[str] = set()
    for i, item in enumerate(templates_raw):
        where = f"templates[{i}]"
        if not isinstance(item, dict):
            _add(where, ConfigContractError("template item must be an object"))
            continue
        tid = str(item.get("template_id") or "")
        if not tid:
            _add(where, ConfigContractError("template item missing template_id"))
            continue
        if item.get("enabled") is False:
            dropped.add(tid)
            continue
        if tid in main_refs or tid in dropped:
            _add(where, ConfigContractError(f"duplicate template_id {tid!r}"))
            continue
        main_cid = item.get("main_container_id")
        if not isinstance(main_cid, str) or not main_cid.strip():
            _add(f"{where}.main_container_id", ConfigContractError(
                "requires a non-empty main_container_id"))
            continue
        side = item.get("sidecar_container_ids") or []
        if not isinstance(side, list) or any(not isinstance(x, str) for x in side):
            _add(f"{where}.sidecar_container_ids", ConfigContractError(
                "must be a list of container ids"))
            continue
        main_refs[tid] = main_cid
        sidecar_refs[tid] = list(side)

    # ---- 阶段 2:容器逐项按角色校验
    main_ids = set(main_refs.values())
    sidecar_ids = set().union(*sidecar_refs.values()) if sidecar_refs else set()
    dual = sorted(main_ids & sidecar_ids)
    if dual:
        _add("containers", ConfigContractError(
            f"containers cannot be both main and sidecar referenced: {dual}"))
    referenced = main_ids | sidecar_ids
    container_specs: dict[str, dict[str, Any]] = {}
    seen_cids: set[str] = set()
    for i, item in enumerate(containers_raw):
        where = f"containers[{i}]"
        if not isinstance(item, dict):
            _add(where, ConfigContractError("container item must be an object"))
            continue
        cid = item.get("container_id")
        if not isinstance(cid, str) or not cid:
            _add(f"{where}.container_id", ConfigContractError(
                "must be a non-empty string"))
            continue
        if cid in seen_cids:
            _add(f"{where}.container_id", ConfigContractError(
                f"duplicate container_id {cid!r}"))
            continue
        seen_cids.add(cid)
        if cid not in referenced:
            _add(f"{where}.container_id", ConfigContractError(
                f"not referenced by any template: {cid!r}"))
            continue
        role = MAIN_ROLE if cid in main_ids else SIDECAR_ROLE
        try:
            container_specs[cid] = parse_container_spec(item, where, role=role)
        except Exception as exc:
            if not is_contract_error(exc):
                raise
            _add(where, exc)
    for cid in sorted(referenced - set(container_specs)):
        _add(f"containers[{cid}]", ConfigContractError(
            "referenced container not present in the containers section"))

    # ---- 阶段 3:模板构造(含 volumes 交叉校验/水合)
    for item in templates_raw:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("template_id") or "")
        if not tid or tid in dropped or tid not in main_refs:
            continue
        try:
            template_split_kwargs(tid, item, container_specs)
        except Exception as exc:
            if not is_contract_error(exc):
                raise
            _add(f"templates[{tid}]", exc)

    return errors


def format_validation_errors(errors: list[dict[str, str]]) -> str:
    """错误列表 → 单行文案(Manager 拒绝保存时直接展示)。"""
    parts: list[str] = []
    for item in errors:
        path = str(item.get("path") or "").strip()
        message = str(item.get("message") or "").strip()
        parts.append(f"{path}: {message}" if path else message)
    return "; ".join(part for part in parts if part)
