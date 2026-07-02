from __future__ import annotations

import uuid
from typing import Any

from a2a.types.a2a_pb2 import Message, SendMessageRequest
from google.protobuf.struct_pb2 import Struct, Value

from common.response_wrapper import wrap_agent_event, wrap_workflow_event, wrap_sub_task_event
from .base import Channel, ParsedRequest
from .observability import log_channel_event


class MobileBankChannel(Channel):
    name = "mobile_bank"

    def parse_request(
        self,
        body: dict[str, Any],
        *,
        path_params: dict[str, Any],
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ParsedRequest:
        if not isinstance(body, dict):
            raise ValueError("request body must be a dict")

        conversation_id = str(
            path_params.get("conversation_id")
            or path_params.get("conv_id")
            or body.get("conversation_id")
            or ""
        )
        agent_id = str(path_params.get("agent_id") or body.get("agent_id") or "")
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")

        parsed = ParsedRequest(
            conversation_id=conversation_id,
            agent_id=agent_id,
            query=self._extract_query(body),
            body=body,
            headers=headers or {},
            params=params or {},
            stream=bool(body.get("stream", True)),
            trace_id=self._extract_trace_id(body, headers or {}),
        )
        log_channel_event(
            action="CHANNEL_PARSE_REQUEST",
            channel=self.name,
            conversation_id=parsed.conversation_id,
            payload={
                "agent_id": parsed.agent_id,
                "stream": parsed.stream,
                "query_preview": parsed.query[:80],
                "trace_id": parsed.trace_id,
                "params_keys": sorted(parsed.params.keys()),
                "header_keys": sorted(parsed.headers.keys()),
            },
        )
        return parsed

    def build_message(self, parsed: ParsedRequest) -> SendMessageRequest:
        body_struct = Struct()
        body_struct.update(
            {
                "body": parsed.body,
                "params": parsed.params,
                "headers": parsed.headers,
            }
        )

        body_value = Value()
        body_value.struct_value.CopyFrom(body_struct)

        msg = Message()
        msg.message_id = str(uuid.uuid4())
        msg.context_id = parsed.conversation_id
        msg.task_id = ""
        msg.role = 1

        text_part = msg.parts.add()
        text_part.text = parsed.query

        data_part = msg.parts.add()
        data_part.data.CopyFrom(body_value)

        request = SendMessageRequest(message=msg)
        log_channel_event(
            action="CHANNEL_BUILD_MESSAGE",
            channel=self.name,
            conversation_id=parsed.conversation_id,
            payload={
                "agent_id": parsed.agent_id,
                "message_id": msg.message_id,
                "parts": len(msg.parts),
                "has_query": bool(parsed.query),
                "trace_id": parsed.trace_id,
            },
        )
        return request

    def format_event(
        self,
        event: dict[str, Any],
        *,
        agent_id: str,
        conversation_id: str,
        elapsed: float,
    ) -> dict[str, Any] | None:
        event_type = event.get("type")
        data = event.get("data") or {}
        if not event_type or not isinstance(data, dict):
            log_channel_event(
                action="CHANNEL_FORMAT_SKIP",
                channel=self.name,
                event_type=str(event_type or ""),
                conversation_id=conversation_id,
                payload={"reason": "missing_type_or_invalid_data"},
            )
            return None
        if event_type == "versatile_proxy":
            workflow_event = data.get("event")
            workflow_data = data.get("data") if isinstance(data.get("data"), dict) else {}
            if not workflow_event:
                return None
            return wrap_workflow_event(
                event_kind=str(workflow_event),
                data=workflow_data,
                agent_id=agent_id,
                conversation_id=conversation_id,
                elapsed=elapsed,
            )

        if event_type == "completed":
            return None

        if event_type in ("failed", "input_required"):
            content = str(data.get("content") or data.get("error") or "")
            return wrap_agent_event(
                event_type="interrupt_start",
                content=content,
                data={},
                agent_id=agent_id,
                conversation_id=conversation_id,
                elapsed=elapsed,
                success=event_type != "failed",
                error=str(data.get("error") or "") if event_type == "failed" else "",
            )

        if event_type == "sub_task":
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            node_kind = str(data.get("node_kind") or "agent")
            inner_meta = self._extract_inner_meta(inner, node_kind=node_kind)
            return wrap_sub_task_event(
                sub_task_path=[str(p) for p in data.get("sub_task_path") or []],
                node_kind=node_kind,
                inner_meta=inner_meta,
                agent_id=agent_id,
                conversation_id=conversation_id,
                elapsed=elapsed,
            )

        payload = dict(data)
        content = str(payload.pop("content", "") or "")
        plugin = str(payload.pop("plugin", "") or "")
        nested = payload.pop("data", None)
        if isinstance(nested, dict):
            payload = nested
        return wrap_agent_event(
            event_type=str(event_type),
            content=content,
            data=payload,
            agent_id=agent_id,
            conversation_id=conversation_id,
            elapsed=elapsed,
            plugin=plugin,
        )

    @staticmethod
    def _extract_inner_meta(frame: dict[str, Any], *, node_kind: str) -> dict[str, Any]:
        if not isinstance(frame, dict):
            return {"kind": "agent", "type": "thought", "content": "", "data": {}}
        event_kind = str(frame.get("event") or "")
        if node_kind == "workflow" and event_kind not in ("node_start", "node_end"):
            return {
                "kind": "workflow",
                "type": event_kind or "message",
                "data": frame.get("data") if isinstance(frame.get("data"), dict) else frame,
            }
        if event_kind in ("node_start", "node_end"):
            return {"kind": "lifecycle", "data": frame}
        payload = dict(frame)
        event_type = str(payload.pop("type", "") or event_kind or "thought")
        content = str(payload.pop("content", "") or "")
        plugin = str(payload.pop("plugin", "") or "")
        return {
            "kind": "agent",
            "type": event_type,
            "content": content,
            "data": payload,
            "plugin": plugin,
            "display": frame.get("display"),
        }

    @staticmethod
    def _extract_query(body: dict[str, Any]) -> str:
        if isinstance(body.get("input"), dict):
            query = body["input"].get("query", "")
            if query:
                return str(query)
        if isinstance(body.get("custom_data"), dict):
            inputs = body["custom_data"].get("inputs", {})
            if isinstance(inputs, dict):
                return str(inputs.get("query", "") or "")
        return ""

    @staticmethod
    def _extract_trace_id(body: dict[str, Any], headers: dict[str, Any]) -> str:
        for key in ("trace_id", "traceid", "x-trace-id", "x-request-id"):
            value = body.get(key)
            if value:
                return str(value)
        for key in ("trace_id", "traceid", "x-trace-id", "x-request-id"):
            value = headers.get(key) or headers.get(key.upper())
            if value:
                return str(value)
        return ""
