#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""
DSL 工作流依赖解析：从导出 JSON 的 dependencies.workflows 解析子工作流，实现 IWorkflowLoader，
使 ExecutorWorkflow.compile 无需查库。

不修改 openjiuwen 与 openjiuwen_studio 源码；与 workflow_ir_builder 模块配合使用。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from openjiuwen.core.workflow.workflow import Workflow as InvokableWorkflow
from openjiuwen_studio.core.common import dsl as studio_dsl
from openjiuwen_studio.core.executor.workflow.context import Context
from openjiuwen_studio.core.executor.workflow.workflow import IWorkflowLoader, Workflow as ExecutorWorkflow

from runtime_support.http_response_contract import LowcodeApiResponseCode
from runtime_support.runtime_env import llm_api_key_env_var_name, resolve_llm_api_key_from_env


WorkflowKey = Tuple[str, str]


class WorkflowLlmApiKeyMissingError(Exception):
    """DSL LLM/意图/提问器节点：可解析的 LLM_KEY__* 与 JSON 内 api_key 均未配置。"""


def strip_dependencies(wf: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in wf.items() if k != "dependencies"}


def collect_workflow_registry(root: Dict[str, Any]) -> Dict[WorkflowKey, Dict[str, Any]]:
    """扁平化收集所有嵌套 dependencies.workflows，字典键为二元组 (id, version)。"""
    reg: Dict[WorkflowKey, Dict[str, Any]] = {}

    def walk(deps: Any) -> None:
        if not isinstance(deps, dict):
            return
        for wf in deps.get("workflows") or []:
            if not isinstance(wf, dict):
                continue
            wid = str(wf.get("id") or "").strip()
            if not wid:
                continue
            wver = str(wf.get("version") or "draft").strip() or "draft"
            key = (wid, wver)
            if key not in reg:
                reg[key] = wf
            walk(wf.get("dependencies"))

    walk(root.get("dependencies"))
    return reg


def _inject_llm_into_component(comp: Dict[str, Any]) -> None:
    if not isinstance(comp, dict):
        return
    t = comp.get("type")
    try:
        ti = int(t) if t is not None else -1
    except (TypeError, ValueError):
        ti = -1
    if ti == int(studio_dsl.ComponentType.COMPONENT_TYPE_LOOP):
        cfg = comp.get("configs") or {}
        lb = cfg.get("loop_body") or {}
        for c in lb.get("components") or []:
            _inject_llm_into_component(c)
        return
    if ti not in (
        int(studio_dsl.ComponentType.COMPONENT_TYPE_LLM),
        int(studio_dsl.ComponentType.COMPONENT_TYPE_INTENT),
        int(studio_dsl.ComponentType.COMPONENT_TYPE_QUESTION),
    ):
        return

    cfg = comp.get("configs") or {}
    model = cfg.get("model")
    if not isinstance(model, dict):
        return
    mcc = model.get("model_client_config") or {}
    if not isinstance(mcc, dict):
        return
    base_url = str(mcc.get("api_base") or "").strip()
    envn = llm_api_key_env_var_name(base_url)
    env_val = resolve_llm_api_key_from_env(base_url)
    if "<SLUG_FROM_BASE_URL>" in envn:
        return

    json_val = str(mcc.get("api_key") or "").strip()
    if env_val:
        mcc["api_key"] = env_val
    elif json_val:
        mcc["api_key"] = json_val
    else:
        cid = str(comp.get("id") or comp.get("component_id") or "").strip() or "?"
        raise WorkflowLlmApiKeyMissingError(
            LowcodeApiResponseCode.LLM_API_KEY_MISSING.format_message(env_var=envn)
            + f" (component id={cid})"
        )

    model["model_client_config"] = mcc
    cfg["model"] = model
    comp["configs"] = cfg


def inject_llm_api_keys_into_workflow_tree(wf: Dict[str, Any]) -> None:
    """按 api_base 解析 LLM_KEY__*：仅当对应环境变量非空时覆盖 api_key，否则保留 DSL 内配置；二者皆空则抛 WorkflowLlmApiKeyMissingError。"""
    for comp in wf.get("components") or []:
        if isinstance(comp, dict):
            _inject_llm_into_component(comp)


def _scalar_endpoint_from_config(value: Any) -> Any:
    """若 connection 的 source 或 target 被写成 list，取第一个元素；仅本应用入口兜底。"""
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def _normalize_connection_endpoints_in_workflow_dict(wf: Dict[str, Any]) -> None:
    conns = wf.get("connections")
    if isinstance(conns, list):
        for c in conns:
            if not isinstance(c, dict):
                continue
            c["source"] = _scalar_endpoint_from_config(c.get("source"))
            c["target"] = _scalar_endpoint_from_config(c.get("target"))
    for comp in wf.get("components") or []:
        if not isinstance(comp, dict):
            continue
        try:
            ti = int(comp.get("type")) if comp.get("type") is not None else -1
        except (TypeError, ValueError):
            ti = -1
        if ti != int(studio_dsl.ComponentType.COMPONENT_TYPE_LOOP):
            continue
        cfg = comp.get("configs") or {}
        lb = cfg.get("loop_body")
        if isinstance(lb, dict):
            _normalize_connection_endpoints_in_workflow_dict(lb)


def workflow_dict_to_dl_workflow(wf: Dict[str, Any]) -> studio_dsl.Workflow:
    stripped = strip_dependencies(wf)
    _normalize_connection_endpoints_in_workflow_dict(stripped)
    inject_llm_api_keys_into_workflow_tree(stripped)
    return studio_dsl.Workflow.model_validate(stripped)


class DependencyWorkflowLoader(IWorkflowLoader):
    """按 id/version 在 dependencies 扁平表中查找子工作流并递归 compile。"""

    def __init__(
        self,
        registry: Dict[WorkflowKey, Dict[str, Any]],
        space_id: str,
        current_user: Dict[str, Any],
    ) -> None:
        self._registry = registry
        self._space_id = space_id
        self._current_user = current_user
        self._cache: Dict[WorkflowKey, InvokableWorkflow] = {}
        self._compiling: set[WorkflowKey] = set()

    def _resolve(self, wid: str, version: str) -> tuple[Dict[str, Any], WorkflowKey]:
        vid = str(wid or "").strip()
        ver = str(version or "").strip() or "draft"
        key: WorkflowKey = (vid, ver)
        d = self._registry.get(key)
        if d is None and ver != "draft":
            key = (vid, "draft")
            d = self._registry.get(key)
        if d is None:
            raise ValueError(
                f"dependencies.workflows 中未找到子工作流 id={wid!r} version={version!r}，"
                f"已注册 id 列表: {[k[0] for k in self._registry]}"
            )
        return d, key

    async def get_compiled_workflow(
        self,
        context: Context,
        workflow_id: str,
        version: str,
        space_id: str,
        current_user: Dict[str, Any],
    ) -> InvokableWorkflow:
        wf_dict, cache_key = self._resolve(workflow_id, version)
        if cache_key in self._cache:
            return self._cache[cache_key]
        if cache_key in self._compiling:
            raise ValueError(f"子工作流循环依赖: id={cache_key[0]!r} version={cache_key[1]!r}")
        self._compiling.add(cache_key)
        try:
            dl = workflow_dict_to_dl_workflow(wf_dict)
            user = current_user if current_user is not None else self._current_user
            executor = ExecutorWorkflow(dl, space_id, user)
            compiled = await executor.compile(context, loader=self)
            self._cache[cache_key] = compiled
            return compiled
        finally:
            self._compiling.discard(cache_key)


def unwrap_workflow_document(ir: Dict[str, Any]) -> Dict[str, Any]:
    """若导出为外层 workflow 键包裹的内层 DSL，则合并 dependencies 后返回内层字典。"""
    if isinstance(ir.get("components"), list) and isinstance(ir.get("connections"), list):
        return ir
    w = ir.get("workflow")
    if isinstance(w, dict) and isinstance(w.get("components"), list):
        merged = dict(w)
        if isinstance(ir.get("dependencies"), dict) and "dependencies" not in w:
            merged["dependencies"] = ir["dependencies"]
        return merged
    return ir


def looks_like_dsl_workflow_export(ir: Dict[str, Any]) -> bool:
    ir = unwrap_workflow_document(ir)
    return isinstance(ir.get("components"), list) and isinstance(ir.get("connections"), list)
