# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""
VersatileA2AGateway - A2A 网关协议适配器。

将 VA 内部统一入参转换为 A2A 1.0 JSON-RPC SendStreamingMessage 请求，
解析 A2A 1.0 SSE 响应（taskArtifactUpdate / taskStatusUpdate），
映射为 VA 统一 AdapterEvent。

data_proxy 输出格式与低码工作流一致（{"event":"message","data":{"text":"..."}}），
上游 a2a_service 和前端无需适配。
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from loguru import logger

from adapters.versatile_proxy import VersatileProxy, VersatileStreamCtx
from event.events import (
    AdapterEvent,
    DataProxyContent,
    ExecutionCompletedContent,
    ExecutionInputRequiredContent,
)


class VersatileA2AGateway(VersatileProxy):
    """A2A 网关协议适配器。

    职责：
    1. 拼 URL：{a2a_gateway_base}/a2a/{agent_card_name}
    2. 构造 Header：token(必选) + userId(必选) + B3/X-Biz-Tag(可选透传)
    3. 构造 A2A 1.0 SendStreamingMessage 请求体（configuration 由 VA 固定生成）
    4. 解析 A2A 1.0 SSE：taskArtifactUpdate / taskStatusUpdate
    5. 映射为 VA 统一 AdapterEvent，data_proxy 格式与低码工作流一致
    """

    def __init__(
        self,
        a2a_gateway_base: str,
        agent_card_name: str,
        token: str,
        url_template: str,
        timeout: int = 600,
        headers_template: Optional[dict] = None,
        forward_header_whitelist: Optional[set[str]] = None,
        workflow_result_node: Optional[str] = None,
    ) -> None:
        super().__init__(url_template, timeout, headers_template, forward_header_whitelist)
        self._a2a_gateway_base = a2a_gateway_base
        self._agent_card_name = agent_card_name
        self._token = token
        # workflow_result_node 保留但不使用，A2A Gateway 模式按方案 D 提取结果
        self._workflow_result_node = workflow_result_node
        # dispatch_stream 入口设置，提前初始化以支持直接调用钩子方法测试
        self._conv_id: str = ""
        self._trace_id: str = ""
        self._passed_headers: dict = {}
        self._cached_user_id: str = ""

    # ── 钩子方法覆盖 ──────────────────────────────────────────

    def _build_url(self, conv_id: str) -> str:
        url = self._url_template.format(
            a2a_gateway_base=self._a2a_gateway_base,
            agent_card_name=self._agent_card_name,
        )
        logger.info(f"[VersatileA2AGateway] URL: {url}, conv_id={conv_id}")
        return url

    def _build_headers(self, headers: Optional[dict] = None) -> dict:
        merged = super()._build_headers(headers)

        # 必选 Header
        merged["token"] = self._token
        self._cached_user_id = self._extract_user_id()
        merged["userId"] = self._cached_user_id
        logger.debug(f"[VersatileA2AGateway] 必选 Header: token=***, userId={merged['userId']}")

        # 可选 Header（上游有则透传，无则不写）
        if self._trace_id or self._passed_headers.get("x-b3-traceid"):
            merged["X-B3-TraceId"] = self._trace_id or self._passed_headers.get("x-b3-traceid", "")
        if self._passed_headers.get("x-b3-parentspanid"):
            merged["X-B3-ParentSpanId"] = self._passed_headers["x-b3-parentspanid"]
            merged["X-B3-SpanId"] = uuid.uuid4().hex[:16]
        if self._passed_headers.get("x-b3-sampled"):
            merged["X-B3-Sampled"] = self._passed_headers["x-b3-sampled"]
        if self._passed_headers.get("x-biz-tag"):
            merged["X-Biz-Tag"] = self._passed_headers["x-biz-tag"]

        return merged

    def _build_request_body(self, body: dict) -> dict:
        inputs = (body.get("custom_data") or {}).get("inputs") or body.get("input") or {}
        query = inputs.get("query", "")

        request_body = {
            "jsonrpc": "2.0",
            "id": f"call-versatile-{uuid.uuid4()}",
            "method": "SendStreamingMessage",
            "params": {
                "metadata": {
                    "userId": self._cached_user_id,
                    "traceId": self._trace_id,
                    "versatile": {
                        "inputs": inputs,
                    },
                },
                "payload": {
                    "message": {
                        "role": "ROLE_USER",
                        "messageId": f"msg-{uuid.uuid4()}",
                        "conversationId": self._conv_id,
                        "parts": [
                            {"type": "text", "text": query},
                        ],
                        "metadata": {},
                    },
                    "configuration": {
                        "blocking": True,
                        "acceptedOutputModes": ["text/plain"],
                    },
                },
            },
        }
        logger.info(
            f"[VersatileA2AGateway] 构造请求体: method=SendStreamingMessage, "
            f"conversationId={self._conv_id}, query={query!r:.60}"
        )
        logger.debug(
            f"[VersatileA2AGateway] 完整请求体: {json.dumps(request_body, ensure_ascii=False)[:500]}"
        )
        return request_body

    def _process_chunk(self, chunk: str, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            logger.warning(f"[VersatileA2AGateway] 无法解析 SSE 行: {chunk!r:.80}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        # JSON-RPC error
        if "error" in parsed:
            error = parsed["error"]
            ctx.completed = True
            ctx.is_failed = True
            ctx.error_message = json.dumps(error, ensure_ascii=False)
            logger.error(f"[VersatileA2AGateway] JSON-RPC error: {error}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

        result = parsed.get("result", {})
        if not isinstance(result, dict):
            logger.warning(f"[VersatileA2AGateway] result 非 dict，透传: {chunk!r:.80}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]
        payload = result.get("payload", {})

        if "taskArtifactUpdate" in payload:
            return self._handle_artifact_update(payload["taskArtifactUpdate"], ctx)

        if "taskStatusUpdate" in payload:
            return self._handle_status_update(payload["taskStatusUpdate"], ctx)

        # 未知类型 - 透传
        logger.debug(f"[VersatileA2AGateway] 未知 payload 类型，透传: {chunk!r:.80}")
        return [AdapterEvent(data_proxy=DataProxyContent(raw_data=chunk))]

    def _on_stream_end(self, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        if not ctx.completed:
            logger.info(f"[VersatileA2AGateway] 流关闭未收到 status-update, 兜底 INPUT_REQUIRED, conv_id={self._conv_id}")
            return [AdapterEvent(execution_input_required=ExecutionInputRequiredContent())]

        if ctx.input_required:
            logger.info(f"[VersatileA2AGateway] INPUT_REQUIRED, conv_id={self._conv_id}")
            return [AdapterEvent(execution_input_required=ExecutionInputRequiredContent())]

        if ctx.is_failed or ctx.execution_result:
            logger.info(
                f"[VersatileA2AGateway] 终态: is_failed={ctx.is_failed}, "
                f"result={ctx.execution_result!r:.60}, conv_id={self._conv_id}"
            )
            return [AdapterEvent(execution_completed=ExecutionCompletedContent(
                is_failed=ctx.is_failed,
                result=ctx.execution_result or "",
                error_message=ctx.error_message,
            ))]

        return []

    # ── 内部方法 ──────────────────────────────────────────────

    def _extract_user_id(self) -> str:
        """从 passed_headers 中大小写不敏感地提取 userId。

        a2a_service 通过 Starlette 的 dict(request.headers) 传入 headers，
        所有 key 会被转为小写（如 x-user-id / userid / cust-userid）。
        此处按优先级依次查找：userid > x-user-id > cust-userid。
        """
        for key, value in self._passed_headers.items():
            key_lower = key.lower()
            if key_lower in ("userid", "x-user-id", "cust-userid") and value:
                return value
        return ""

    def _handle_artifact_update(self, update: dict, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        artifact = update.get("artifact", {})
        artifact_id = artifact.get("artifactId", "_default")
        append = update.get("append", False)
        last_chunk = update.get("lastChunk", False)

        # 提取所有 text part 的文本
        parts = artifact.get("parts", [])
        if not isinstance(parts, list):
            parts = []
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and part.get("type") == "text"
        )

        # 按 append 累积（方案 D）
        if append and artifact_id in ctx.artifact_texts:
            ctx.artifact_texts[artifact_id] += text
        else:
            ctx.artifact_texts[artifact_id] = text
        ctx.last_artifact_id = artifact_id

        logger.debug(
            f"[VersatileA2AGateway] artifact: id={artifact_id}, append={append}, "
            f"last_chunk={last_chunk}, text={text!r:.60}"
        )

        # data_proxy 转换为低码工作流统一格式（空文本跳过，避免前端收到空消息）
        if not text:
            return []
        frame = {"event": "message", "data": {"text": text}}
        raw = json.dumps(frame, ensure_ascii=False)
        logger.debug(f"[VersatileA2AGateway] 输出 AdapterEvent(data_proxy): {raw[:200]}")
        return [AdapterEvent(data_proxy=DataProxyContent(raw_data=raw))]

    def _handle_status_update(self, update: dict, ctx: VersatileStreamCtx) -> list[AdapterEvent]:
        status = update.get("status", update)  # 兼容 status 嵌套或直接在 update 上
        state = status.get("state", update.get("state", ""))
        if not isinstance(state, str):
            state = str(state) if state is not None else ""
        state_lower = state.replace("TASK_STATE_", "").lower()

        logger.info(f"[VersatileA2AGateway] status-update: state={state} ({state_lower}), conv_id={self._conv_id}")

        if state_lower == "completed":
            ctx.completed = True
            # 取最后一个 artifactId 的累积文本
            if ctx.last_artifact_id and ctx.last_artifact_id in ctx.artifact_texts:
                ctx.execution_result = ctx.artifact_texts[ctx.last_artifact_id]
                logger.info(
                    f"[VersatileA2AGateway] 结果提取: artifact_id={ctx.last_artifact_id}, "
                    f"result={ctx.execution_result!r:.60}"
                )
            else:
                # 降级：从 status.message 提取
                ctx.execution_result = self._extract_text_from_status_message(status)
                if ctx.execution_result:
                    logger.info(f"[VersatileA2AGateway] 结果提取(降级message): {ctx.execution_result!r:.60}")

            # 补发 end 帧（与低码工作流格式一致）
            logger.debug(f"[VersatileA2AGateway] 补发 end 帧")
            logger.debug(f"[VersatileA2AGateway] 输出 AdapterEvent(data_proxy): {{\"event\":\"end\"}}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data='{"event":"end"}'))]

        if state_lower == "failed":
            ctx.completed = True
            ctx.is_failed = True
            ctx.error_message = self._extract_text_from_status_message(status) or "A2A Gateway 返回 FAILED"
            logger.error(f"[VersatileA2AGateway] FAILED: error={ctx.error_message!r:.100}")

            # 转换为低码 error 帧格式
            error_frame = json.dumps({
                "event": "error",
                "data": {"code": "", "message": ctx.error_message},
            }, ensure_ascii=False)
            logger.debug(f"[VersatileA2AGateway] 输出 AdapterEvent(data_proxy): {error_frame[:200]}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=error_frame))]

        if state_lower == "canceled":
            ctx.completed = True
            ctx.is_failed = True
            ctx.error_message = self._extract_text_from_status_message(status) or "A2A Gateway 返回 CANCELED"
            logger.warning(f"[VersatileA2AGateway] CANCELED: error={ctx.error_message!r:.100}")

            error_frame = json.dumps({
                "event": "error",
                "data": {"code": "", "message": ctx.error_message},
            }, ensure_ascii=False)
            logger.debug(f"[VersatileA2AGateway] 输出 AdapterEvent(data_proxy): {error_frame[:200]}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=error_frame))]

        if state_lower == "rejected":
            ctx.completed = True
            ctx.is_failed = True
            ctx.error_message = self._extract_text_from_status_message(status) or "A2A Gateway 返回 REJECTED"
            logger.warning(f"[VersatileA2AGateway] REJECTED: error={ctx.error_message!r:.100}")

            error_frame = json.dumps({
                "event": "error",
                "data": {"code": "", "message": ctx.error_message},
            }, ensure_ascii=False)
            logger.debug(f"[VersatileA2AGateway] 输出 AdapterEvent(data_proxy): {error_frame[:200]}")
            return [AdapterEvent(data_proxy=DataProxyContent(raw_data=error_frame))]

        if state_lower == "input_required":
            ctx.completed = True
            ctx.input_required = True
            return []

        # working / submitted 等非终态 - 忽略
        logger.debug(f"[VersatileA2AGateway] 非终态 status: {state}")
        return []

    @staticmethod
    def _extract_text_from_status_message(status: dict) -> str:
        """从 taskStatusUpdate 的 message.parts 中提取文本。"""
        message = status.get("message")
        if not message or not isinstance(message, dict):
            return ""
        parts = message.get("parts", [])
        return "".join(
            part.get("text", "")
            for part in parts
            if part.get("type") == "text"
        )
