# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

"""
从低码工作流 JSON 构建可执行的 openjiuwen.core.workflow.workflow.Workflow。

支持两种输入：
1. 画布 IR：nodes、edges、model_references、plugins。
2. DSL 导出：components、connections，以及 dependencies.workflows 内嵌子工作流；
   编译时通过 DependencyWorkflowLoader 按 id 解析子工作流，不查业务库。

依赖 openjiuwen_studio 的 WorkflowBase、DSL 转换与 ExecutorWorkflow.compile。
画布 IR 路径下 LLM、提问器、意图节点由 model_references 补全密钥与端点；插件由 plugins 预计算 plugin_tool_configs。
当 api_key 为空时，按 base_url 生成 LLM_KEY__ 前缀的环境变量名，从进程环境变量读取。

运行前需安装 openjiuwen 与 openjiuwen_studio 依赖。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

_APP_DIR = Path(__file__).resolve().parent

from openjiuwen.core.common.logging import logger
from openjiuwen_studio.core.common import dsl as studio_dsl
from openjiuwen_studio.core.common.exceptions import JiuWenComponentException
from openjiuwen_studio.core.common.status_code import StatusCode
from openjiuwen_studio.core.executor.workflow.context import Context
from openjiuwen_studio.core.executor.workflow.workflow import Workflow as ExecutorWorkflow
from openjiuwen_studio.core.manager.convertor.components.code import code_convert
from openjiuwen_studio.core.manager.convertor.components.common import (
    exception_config_convert,
    input_params_convert,
    outputs_convert,
)
from openjiuwen_studio.core.manager.convertor.components.empty import empty_convert
from openjiuwen_studio.core.manager.convertor.components.end import end_convert
from openjiuwen_studio.core.manager.convertor.components.input import input_convert
from openjiuwen_studio.core.manager.convertor.components.intent import (
    _intent_inputs_convert,
    _intent_outputs_convert,
)
from openjiuwen_studio.core.manager.convertor.components.llm import _llm_output_config_convert
from openjiuwen_studio.core.manager.convertor.components.loop import (
    loop_break_convert,
    loop_continue_convert,
    loop_convert,
)
from openjiuwen_studio.core.manager.convertor.components.output import output_convert
from openjiuwen_studio.core.manager.convertor.components.questioner import _output_and_extract_field_convert
from openjiuwen_studio.core.manager.convertor.components.set_variable import set_variable_convert
from openjiuwen_studio.core.manager.convertor.components.start import start_convert
from openjiuwen_studio.core.manager.convertor.components.sub_workflow import sub_workflow_convert
from openjiuwen_studio.core.manager.convertor.components.switch import switch_convert
from openjiuwen_studio.core.manager.convertor.components.text_editor import text_editor_convert
from openjiuwen_studio.core.manager.convertor.components.variable_merge import variable_merge_convert
from openjiuwen_studio.core.manager.convertor.connection import connection_convert
from openjiuwen_studio.core.manager.convertor.validators import validate_canvas_nodes
from openjiuwen_studio.core.manager.convertor.workflow import (
    _friendly_validation_message,
    extract_inputs_and_outputs_from_canvas,
)
from openjiuwen_studio.core.manager.internal.workflow import WorkflowCanvas
from openjiuwen_studio.core.manager.utils.utils import convert_to_properties_format
from openjiuwen_studio.core.utils.exception import log_exception
from openjiuwen_studio.schemas.node import BaseValue, Node
from openjiuwen_studio.schemas.workflow import WorkflowBase

from dsl_workflow_dependency_loader import (
    DependencyWorkflowLoader,
    collect_workflow_registry,
    unwrap_workflow_document,
    workflow_dict_to_dl_workflow,
    looks_like_dsl_workflow_export,
)

from runtime_support.runtime_env import get_bool_env, resolve_llm_api_key_from_env

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------


def inject_api_keys_into_model_references(model_references: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, ref in (model_references or {}).items():
        if not isinstance(ref, dict):
            continue
        cell = dict(ref)
        base_url = str(cell.get("base_url") or "").strip()
        if not str(cell.get("api_key") or "").strip() and base_url:
            api_key = resolve_llm_api_key_from_env(base_url)
            if api_key:
                cell["api_key"] = api_key
        out[str(key)] = cell
    return out


# ---------------------------------------------------------------------------
# plugins -> ToolCompConfig 注册表（纯 IR）
# ---------------------------------------------------------------------------

_PARAM_TYPE_IR = {
    "1": "string",
    "2": "integer",
    "3": "number",
    "4": "boolean",
    "5": "object",
    "6": "array",
    "7": "array",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}

_HTTP_METHOD_IR = {
    "1": "GET",
    "2": "POST",
    "3": "PUT",
    "4": "DELETE",
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
}

_SEND_METHOD_IR = {
    "0": "",
    "1": "header",
    "2": "query",
    "3": "body",
    "header": "header",
    "query": "query",
    "body": "body",
}


def _normalize_plugin_type(plugin_type: Any) -> str:
    v = str(plugin_type or "").lower()
    if v in {"1", "service", "api", "cloud_api", "plugin_type_cloud_api"}:
        return studio_dsl.PluginType.SERVICE
    if v in {"2", "code", "cloud_code", "plugin_type_cloud_code"}:
        return studio_dsl.PluginType.CODE
    return studio_dsl.PluginType.CODE if "code" in v else studio_dsl.PluginType.SERVICE


def _build_param(param: Dict[str, Any]) -> studio_dsl.Param:
    return studio_dsl.Param(
        name=param.get("name") or "",
        description=param.get("desc") or param.get("description") or "",
        type=_PARAM_TYPE_IR.get(str(param.get("type") or "").lower(), "string"),
        required=bool(param.get("is_required") or param.get("required")),
        method=_SEND_METHOD_IR.get(str(param.get("method") or "").lower(), ""),
        default_value=param.get("value") if param.get("value") is not None else param.get("default_value"),
        runtime=bool(param.get("is_runtime", True)),
    )


def _build_plugin_code_config(plugin: Dict[str, Any], tool: Dict[str, Any]) -> Dict[str, Any]:
    return studio_dsl.ToolCompConfig(
        type=studio_dsl.PluginType.CODE,
        tool=studio_dsl.PluginCodeConfig(
            tool_id=str(tool.get("tool_id") or tool.get("id") or ""),
            name=tool.get("name") or "",
            description=tool.get("desc") or tool.get("description") or "",
            language=tool.get("language") or "python",
            code=tool.get("code") or "",
            input_params=[_build_param(p) for p in (tool.get("request_params") or tool.get("inputs") or [])],
            output_params=[
                studio_dsl.ParamConfig(
                    name=p.get("name") or "",
                    type=_PARAM_TYPE_IR.get(str(p.get("type") or "").lower(), "string"),
                )
                for p in (tool.get("response_params") or tool.get("outputs") or [])
            ],
        ).model_dump(),
    ).model_dump()


def _build_plugin_service_config(plugin: Dict[str, Any], tool: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(plugin.get("url") or plugin.get("base_url") or "").rstrip("/")
    path = str(tool.get("path") or tool.get("url") or "")
    if base_url and path and not path.startswith(("http://", "https://")):
        path = f"{base_url}/{path.lstrip('/')}"

    headers = {}
    for header in tool.get("headers") or []:
        hn = header.get("name")
        if hn:
            headers[hn] = header.get("value")

    request_params = list(tool.get("request_params") or [])
    plugin_default_params = list(plugin.get("inputs") or plugin.get("request_params") or [])
    merged: Dict[str, Dict[str, Any]] = {str(p.get("name")): p for p in request_params if p.get("name")}
    for param in plugin_default_params:
        name = param.get("name")
        if name and name not in merged:
            merged[name] = param

    method = _HTTP_METHOD_IR.get(str(tool.get("method") or "").lower(), str(tool.get("method") or "GET").upper())

    return studio_dsl.ToolCompConfig(
        type=studio_dsl.PluginType.SERVICE,
        tool=studio_dsl.RestfulApiSchema(
            tool_id=str(tool.get("tool_id") or tool.get("id") or ""),
            name=tool.get("name") or "",
            description=tool.get("desc") or tool.get("description") or "",
            path=path,
            method=method,
            params=[_build_param(p) for p in merged.values()],
            response=[_build_param(p) for p in (tool.get("response_params") or [])],
            headers=headers,
        ).model_dump(),
    ).model_dump()


def build_plugin_tool_config_map(dependency_plugins: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for plugin in dependency_plugins or []:
        if not isinstance(plugin, dict):
            continue
        plugin_id = str(plugin.get("plugin_id") or plugin.get("id") or "")
        plugin_version = str(plugin.get("plugin_version") or plugin.get("version") or "draft")
        ptype = _normalize_plugin_type(plugin.get("plugin_type"))
        tools = plugin.get("tools") or plugin.get("tool_list") or []
        ver = plugin_version or "draft"
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_id = str(tool.get("tool_id") or tool.get("id") or "")
            if not tool_id:
                continue
            config = (
                _build_plugin_service_config(plugin, tool)
                if ptype == studio_dsl.PluginType.SERVICE
                else _build_plugin_code_config(plugin, tool)
            )
            for key in (f"{plugin_id}:{tool_id}:{ver}", f"{plugin_id}:{tool_id}", tool_id):
                result[key] = config
    return result


# ---------------------------------------------------------------------------
# model_references -> ModelConfig（无 DB）
# ---------------------------------------------------------------------------


def _model_config_from_references(
    model_id: str,
    canvas_model_type: str,
    model_references: Dict[str, Any],
    node_label: str,
) -> studio_dsl.ModelConfig:
    mid = str(model_id).strip()
    ref: Optional[Dict[str, Any]] = None
    if mid and model_references:
        if mid in model_references:
            r = model_references[mid]
            ref = r if isinstance(r, dict) else None
        if ref is None:
            for r in model_references.values():
                if isinstance(r, dict) and str(r.get("model_id")) == mid:
                    ref = r
                    break
    if not ref:
        raise ValueError(f"model_references 缺少模型 id={mid!r}，无法实例化节点 {node_label!r}")

    pr = ref.get("parameters")
    temp, top_p = 0.7, 0.9
    if isinstance(pr, dict):
        temp = float(pr.get("temperature", temp))
        top_p = float(pr.get("top_p", top_p))
    timeout = int(ref.get("timeout") or 60)
    api_key = str(ref.get("api_key") or "").strip()
    base_url = str(ref.get("base_url") or "").strip()
    provider = str(ref.get("provider") or ref.get("model_provider") or "openai")
    model_name = canvas_model_type or str(ref.get("model_type") or ref.get("name") or "")
    return studio_dsl.ModelConfig(
        model_client_config=studio_dsl.ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=base_url,
            timeout=timeout,
            verify_ssl=get_bool_env("LLM_SSL_VERIFY", True),
        ),
        request_config=studio_dsl.ModelRequestConfig(
            model_name=model_name,
            temperature=temp,
            top_p=top_p,
        ),
    )


# ---------------------------------------------------------------------------
# 无 DB 的 LLM / 提问器 / 意图 / 插件 转换
# ---------------------------------------------------------------------------


def _llm_convert_export(node: Any, space_id: str, model_references: Dict[str, Any]) -> studio_dsl.Component:
    data = node.data
    inputs = data.inputs
    if inputs is None or inputs.input_parameters is None:
        raise ValueError("llm node missing inputs.input_parameters")
    if inputs.llm_param is None:
        raise ValueError("llm node missing llm_param")
    if data.outputs is None:
        raise ValueError("llm node missing outputs")

    llm_params = inputs.llm_param
    model_cfg = _model_config_from_references(
        str(llm_params.model.id), llm_params.model.type, model_references, node.id
    )
    llm_configs = studio_dsl.LLMConfig(
        model=model_cfg,
        response_format_type=data.output_format,
        output_config=_llm_output_config_convert(data.outputs),
        template_content=[
            {"role": "system", "content": llm_params.system_prompt.content},
            {"role": "user", "content": llm_params.prompt.content},
        ],
        enable_history=inputs.enable_history,
    )
    return studio_dsl.Component(
        id=getattr(node, "id", ""),
        type=studio_dsl.ComponentType.COMPONENT_TYPE_LLM,
        type_version="1.0.0",
        inputs=input_params_convert(inputs.input_parameters),
        outputs=outputs_convert(data.outputs),
        configs=llm_configs.model_dump(),
        name=data.title,
    )


def _questioner_convert_export(node: Any, space_id: str, model_references: Dict[str, Any]) -> studio_dsl.Component:
    data = node.data
    inputs = data.inputs
    if inputs is None or inputs.input_parameters is None or inputs.llm_param is None:
        raise ValueError("questioner node missing inputs")
    if data.outputs is None:
        raise ValueError("questioner node missing outputs")

    llm_params = inputs.llm_param
    model_cfg = _model_config_from_references(
        str(llm_params.model.id), llm_params.model.type, model_references, node.id
    )
    converted_outputs, converted_fields = _output_and_extract_field_convert(data.outputs)
    questioner_configs = studio_dsl.QuestionerConfig(
        model=model_cfg,
        field_names=converted_fields,
        max_response=inputs.max_response,
        with_chat_history=inputs.enable_history,
    )
    return studio_dsl.Component(
        id=getattr(node, "id", ""),
        type=studio_dsl.ComponentType.COMPONENT_TYPE_QUESTION,
        type_version="1.0.0",
        inputs=input_params_convert(inputs.input_parameters),
        outputs=converted_outputs,
        configs=questioner_configs.model_dump(),
        name=data.title,
    )


def _intent_convert_export(node: Any, space_id: str, model_references: Dict[str, Any]) -> studio_dsl.Component:
    data = node.data
    inputs = data.inputs
    if inputs is None or inputs.llm_param is None:
        raise ValueError("intent node missing llm_param")
    if data.outputs is None:
        raise ValueError("intent node missing outputs")

    llm_params = inputs.llm_param
    user_prompt = llm_params.prompt.content if llm_params.prompt and llm_params.prompt.content else ""
    intents = inputs.intents or []
    category_list = [f"分类{i}" for i in range(1, len(intents) + 1)]
    category_name_list = [intent.name for intent in intents]

    model_cfg = _model_config_from_references(
        str(llm_params.model.id), llm_params.model.type, model_references, node.id
    )
    converted_configs = studio_dsl.IntentDetectionConfig(
        user_prompt=user_prompt,
        category_list=category_list,
        category_name_list=category_name_list,
        model=model_cfg,
        enable_history=inputs.enable_history,
    )
    branches = [studio_dsl.Branch(branch_id=inputs.default_intent)]
    branches.extend(studio_dsl.Branch(branch_id=intent.id) for intent in intents)

    return studio_dsl.Component(
        id=node.id,
        name=data.title,
        type=studio_dsl.ComponentType.COMPONENT_TYPE_INTENT,
        type_version="1.0.0",
        inputs=_intent_inputs_convert(inputs),
        outputs=_intent_outputs_convert(data.outputs),
        configs=converted_configs.model_dump(),
        branches=branches,
    )


def _plugin_convert_export(
    node: Any,
    space_id: str,
    plugin_tool_configs: Dict[str, Dict[str, Any]],
) -> studio_dsl.Component:
    data = node.data
    inputs = data.inputs
    if inputs is None:
        raise TypeError("plugin node inputs is none")

    convert_inputs: Dict[str, Any] = {}
    if inputs.input_parameters is not None:
        # Studio 对 constant+object/array 且 content 为空会 literal_eval 失败，此处补 "{}" / "[]"
        fixed: Dict[str, BaseValue] = {}
        for key, value in inputs.input_parameters.items():
            bv = BaseValue.model_validate(value) if isinstance(value, dict) else value
            if bv.type == "constant" and bv.schema is not None and bv.schema.type in ("object", "array"):
                c = bv.content
                if c is None or (isinstance(c, str) and not c.strip()):
                    bv = bv.model_copy(
                        update={"content": "{}" if bv.schema.type == "object" else "[]"}
                    )
            fixed[key] = bv
        convert_inputs = input_params_convert(fixed)

    exception_conf = studio_dsl.ExceptConfig()
    if data.exception_config is not None:
        exception_conf = exception_config_convert(data.exception_config)
    pp = data.inputs.plugin_param
    lookup_keys = (
        f"{pp.plugin_id}:{pp.tool_id}:{pp.plugin_version or 'draft'}",
        f"{pp.plugin_id}:{pp.tool_id}",
        str(pp.tool_id),
    )
    tool_dump = next((plugin_tool_configs[k] for k in lookup_keys if k in plugin_tool_configs), None)
    if not tool_dump:
        raise ValueError(f"IR plugins 中未找到插件工具，已尝试 keys={list(lookup_keys)}（节点 {node.id}）")

    configs = studio_dsl.ToolCompConfig(
        type=tool_dump.get("type"),
        tool=tool_dump.get("tool") or {},
        exception_config=exception_conf,
    ).model_dump()

    return studio_dsl.Component(
        id=node.id,
        type=studio_dsl.ComponentType.COMPONENT_TYPE_PLUGIN,
        type_version="1.0.0",
        description="",
        inputs=convert_inputs,
        configs=configs,
        name=data.title,
    )


# ---------------------------------------------------------------------------
# component_convert 变体（子图递归共用 model_references / plugin_tool_configs）
# ---------------------------------------------------------------------------

_CONVERT_ERROR_CODES = {
    studio_dsl.ComponentType.COMPONENT_TYPE_START: StatusCode.START_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_LLM: StatusCode.LLM_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_END: StatusCode.END_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_IF: StatusCode.IF_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_LOOP: StatusCode.LOOP_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_INPUT: StatusCode.INPUT_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_OUTPUT: StatusCode.OUTPUT_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_QUESTION: StatusCode.QUESTION_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_CONTINUE: StatusCode.CONTINUE_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_BREAK: StatusCode.BREAK_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_TEXT_EDITOR: StatusCode.TEXTEDITOR_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_INTENT: StatusCode.INTENT_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_SUB_WORKFLOW: StatusCode.SUBWORKFLOW_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_START: StatusCode.EMPTY_START_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_END: StatusCode.EMPTY_END_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_CODE: StatusCode.CODE_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_VARIABLE_MERGE: StatusCode.VARIABLE_MERGE_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_SET_VARIABLE: StatusCode.SET_VARIABLE_COMPONENT_CONVERT_FAILED.code,
    studio_dsl.ComponentType.COMPONENT_TYPE_PLUGIN: StatusCode.PLUGIN_COMPONENT_CONVERT_FAILED.code,
}


def component_convert_for_export(
    edges: List,
    nodes: List,
    space_id: str,
    sub_convert: bool,
    *,
    model_references: Dict[str, Any],
    plugin_tool_configs: Dict[str, Dict[str, Any]],
) -> List[studio_dsl.Component]:
    def convert_loop(n: Any, s: str, sub: bool) -> studio_dsl.Component:
        if sub:
            raise TypeError("loop component can not contain sub loop component")
        c = loop_convert(n)
        blocks = n.blocks
        if not blocks:
            raise ValueError("loop blocks is empty")
        sub_nodes = [Node(**block) for block in blocks]
        sub_comps = component_convert_for_export(
            n.edges,
            sub_nodes,
            s,
            True,
            model_references=model_references,
            plugin_tool_configs=plugin_tool_configs,
        )
        start_id: List[str] = []
        end_id: List[str] = []
        for block in n.blocks:
            sub_node = Node(**block)
            nt = int(sub_node.type)
            if nt == studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_START:
                start_id.append(sub_node.id)
            elif nt == studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_END:
                end_id.append(sub_node.id)
        if not n.edges:
            raise ValueError("loop edges is empty")
        sub_edges = connection_convert(n.edges)
        c.configs = studio_dsl.LoopConfig(
            loop_body=studio_dsl.BaseFlow(
                start_id=start_id,
                end_id=end_id,
                components=sub_comps,
                connections=sub_edges,
            )
        ).model_dump()
        return c

    converters = {
        studio_dsl.ComponentType.COMPONENT_TYPE_START: lambda n, s, sub: start_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_LLM: lambda n, s, sub: _llm_convert_export(n, s, model_references),
        studio_dsl.ComponentType.COMPONENT_TYPE_END: lambda n, s, sub: end_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_IF: lambda n, s, sub: switch_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_LOOP: convert_loop,
        studio_dsl.ComponentType.COMPONENT_TYPE_INPUT: lambda n, s, sub: input_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_OUTPUT: lambda n, s, sub: output_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_QUESTION: lambda n, s, sub: _questioner_convert_export(
            n, s, model_references
        ),
        studio_dsl.ComponentType.COMPONENT_TYPE_CONTINUE: lambda n, s, sub: loop_continue_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_BREAK: lambda n, s, sub: loop_break_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_TEXT_EDITOR: lambda n, s, sub: text_editor_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_INTENT: lambda n, s, sub: _intent_convert_export(
            n, s, model_references
        ),
        studio_dsl.ComponentType.COMPONENT_TYPE_SUB_WORKFLOW: lambda n, s, sub: sub_workflow_convert(n, s),
        studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_START: lambda n, s, sub: empty_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_EMPTY_END: lambda n, s, sub: empty_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_CODE: lambda n, s, sub: code_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_VARIABLE_MERGE: lambda n, s, sub: variable_merge_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_SET_VARIABLE: lambda n, s, sub: set_variable_convert(n),
        studio_dsl.ComponentType.COMPONENT_TYPE_PLUGIN: lambda n, s, sub: _plugin_convert_export(
            n, s, plugin_tool_configs
        ),
    }

    try:
        components: List[studio_dsl.Component] = []
        for node in nodes:
            node_type = int(node.type)
            try:
                converter = converters.get(node_type)
                if not converter:
                    msg = f"不支持的画布组件类型: {node_type}"
                    logger.error(msg)
                    raise JiuWenComponentException(
                        code=StatusCode.COMPONENT_CONVERT_FAILED.code,
                        message=StatusCode.COMPONENT_CONVERT_FAILED.errmsg.format(msg=msg),
                        component_id=node.id,
                        component_type=node_type,
                        error_stage="convert",
                    )
                comp = converter(node, space_id, sub_convert)
            except Exception as ce:
                code = _CONVERT_ERROR_CODES.get(node_type, StatusCode.COMPONENT_CONVERT_FAILED.code)
                raise JiuWenComponentException(
                    code=code,
                    message=str(ce),
                    component_id=node.id,
                    component_type=node_type,
                    error_stage="convert",
                ) from ce
            if comp:
                components.append(comp)
        return components
    except (TypeError, ValueError, AttributeError) as e:
        log_exception(e)
        raise ValueError(f"Invalid workflow schema or input: {e}") from e


def workflow_convert_for_export(
    workflow_info: Any,
    *,
    skip_validation: bool,
    model_references: Dict[str, Any],
    plugin_tool_configs: Dict[str, Dict[str, Any]],
) -> studio_dsl.Workflow:
    try:
        workflow_schema = json.loads(getattr(workflow_info, "workflow_schema", "{}") or "{}")
        try:
            canvas = WorkflowCanvas(**workflow_schema)
        except ValidationError as e:
            errs = e.errors() or []
            err = errs[0] if errs else {"msg": str(e), "loc": []}
            hint, _, component_type, component_id = _friendly_validation_message(err, workflow_schema)
            code = StatusCode.COMPONENT_CONFIG_INVALID.code
            if component_type == studio_dsl.ComponentType.COMPONENT_TYPE_LLM:
                code = StatusCode.LLM_COMPONENT_CONFIG_INVALID.code
            raise JiuWenComponentException(
                code=code,
                message=StatusCode.COMPONENT_CONFIG_INVALID.errmsg.format(msg=hint),
                component_id=component_id,
                component_type=component_type,
                error_stage="validate",
            ) from e
        if not skip_validation:
            validate_canvas_nodes(canvas)

        components = component_convert_for_export(
            canvas.edges,
            canvas.nodes,
            workflow_info.space_id,
            False,
            model_references=model_references,
            plugin_tool_configs=plugin_tool_configs,
        )

        input_properties, input_requires = convert_to_properties_format(workflow_info.input_parameters)
        inputs = {"type": "object", "properties": input_properties, "required": input_requires}
        output_properties, _ = convert_to_properties_format(workflow_info.output_parameters)

        start_id: List[str] = []
        end_id: List[str] = []
        for c in components:
            if c.type == studio_dsl.ComponentType.COMPONENT_TYPE_START:
                start_id.append(c.id)
            elif c.type == studio_dsl.ComponentType.COMPONENT_TYPE_END:
                end_id.append(c.id)

        version = getattr(workflow_info, "workflow_version", "1.0.0") or "1.0.0"
        return studio_dsl.Workflow(
            inputs=inputs,
            outputs=output_properties,
            start_id=start_id,
            end_id=end_id,
            id=getattr(workflow_info, "workflow_id", ""),
            name=getattr(workflow_info, "name", "Unnamed Workflow"),
            version=version,
            description=getattr(workflow_info, "desc", "") or "",
            components=components,
            connections=connection_convert(canvas.edges),
        )
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        raise ValueError(f"Invalid workflow schema or input: {str(e)}") from e


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def _apply_dsl_top_level_overrides(
    ir: Dict[str, Any],
    workflow_id: Optional[str],
    name: Optional[str],
    desc: Optional[str],
) -> Dict[str, Any]:
    out = dict(ir)
    if workflow_id:
        out["id"] = workflow_id
    if name is not None:
        out["name"] = name
    if desc is not None:
        out["description"] = desc
    return out


async def build_core_workflow_from_dsl_dict(
    ir: Dict[str, Any],
    *,
    workflow_id: Optional[str] = None,
    name: Optional[str] = None,
    desc: Optional[str] = None,
    space_id: str = "default",
    current_user: Optional[Dict[str, Any]] = None,
    skip_validation: bool = True,
) -> Any:
    """由 DSL 形态 JSON（含 dependencies.workflows）构建可运行 Workflow，子工作流不查库。"""
    del skip_validation
    ir = unwrap_workflow_document(ir)
    if not looks_like_dsl_workflow_export(ir):
        raise ValueError("build_core_workflow_from_dsl_dict 需要 components + connections 的 DSL 导出")
    registry = collect_workflow_registry(ir)
    ir2 = _apply_dsl_top_level_overrides(ir, workflow_id, name, desc)
    dl_workflow = workflow_dict_to_dl_workflow(ir2)
    user = current_user or {"user_id": "local", "space_id": space_id}
    loader = DependencyWorkflowLoader(registry, space_id, user)
    executor_wf = ExecutorWorkflow(dl_workflow, space_id, user)
    return await executor_wf.compile(Context(), loader=loader)


async def build_core_workflow_from_ir_dict(
    ir: Dict[str, Any],
    *,
    workflow_id: Optional[str] = None,
    name: Optional[str] = None,
    desc: Optional[str] = None,
    space_id: str = "default",
    current_user: Optional[Dict[str, Any]] = None,
    skip_validation: bool = True,
) -> Any:
    ir0 = unwrap_workflow_document(ir)
    if looks_like_dsl_workflow_export(ir0):
        return await build_core_workflow_from_dsl_dict(
            ir0,
            workflow_id=workflow_id,
            name=name,
            desc=desc,
            space_id=space_id,
            current_user=current_user,
            skip_validation=skip_validation,
        )

    src = ir0
    wid = (workflow_id or src.get("workflow_id") or src.get("id") or "").strip() or "local_workflow"
    wname = (name or src.get("name") or src.get("workflow_name") or "Workflow from IR").strip() or "Workflow"
    wdesc = (desc or src.get("description") or src.get("desc") or "").strip() or ""
    wver = str(src.get("workflow_version") or src.get("version") or "draft")

    canvas = {"nodes": src.get("nodes") or [], "edges": src.get("edges") or []}
    input_parameters, output_parameters = extract_inputs_and_outputs_from_canvas(canvas)
    refs = inject_api_keys_into_model_references(src.get("model_references"))
    plugin_map = build_plugin_tool_config_map(src.get("plugins") or [])

    wf_base = WorkflowBase(
        workflow_id=wid,
        space_id=space_id,
        workflow_version=wver,
        name=wname,
        desc=wdesc or None,
        schema=json.dumps(canvas, ensure_ascii=False),
        input_parameters=input_parameters,
        output_parameters=output_parameters,
    )

    dl_workflow = workflow_convert_for_export(
        wf_base,
        skip_validation=skip_validation,
        model_references=refs,
        plugin_tool_configs=plugin_map,
    )

    user = current_user or {"user_id": "local", "space_id": space_id}
    executor_wf = ExecutorWorkflow(dl_workflow, space_id, user)
    return await executor_wf.compile(Context(), loader=None)


async def build_core_workflow_from_ir_file(path: str | Path, **kwargs: Any) -> Any:
    p = Path(path).expanduser().resolve()
    ir = json.loads(p.read_text(encoding="utf-8"))
    return await build_core_workflow_from_ir_dict(ir, **kwargs)
