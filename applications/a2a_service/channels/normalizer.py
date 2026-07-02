from __future__ import annotations

import json
from typing import Any

from a2a.types.a2a_pb2 import (
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
)
from google.protobuf.json_format import MessageToDict


class EventNormalizer:
    @staticmethod
    def normalize(event: Any) -> dict[str, Any] | None:
        if isinstance(event, TaskArtifactUpdateEvent):
            return EventNormalizer._normalize_artifact(event)
        if isinstance(event, TaskStatusUpdateEvent):
            return EventNormalizer._normalize_status(event)
        return None

    @staticmethod
    def _normalize_artifact(event: TaskArtifactUpdateEvent) -> dict[str, Any] | None:
        text_content = ""
        data_dict: dict[str, Any] = {}
        for part in event.artifact.parts:
            kind = part.WhichOneof("content")
            if kind == "text":
                text_content = part.text or ""
            elif kind == "data":
                data_dict = MessageToDict(part.data)

        metadata_type = ""
        if event.artifact.HasField("metadata"):
            meta = MessageToDict(event.artifact.metadata)
            metadata_type = str(meta.get("type") or "")

        if metadata_type == "versatile_proxy":
            return {"type": "versatile_proxy", "data": data_dict}

        if "event" in data_dict and isinstance(data_dict.get("data"), dict):
            return {
                "type": "versatile_proxy",
                "data": {
                    "event": data_dict.get("event"),
                    "data": data_dict.get("data") or {},
                },
            }

        event_type = data_dict.get("type") or metadata_type
        if event_type:
            frame = dict(data_dict)
            frame.pop("type", None)
            if text_content and "content" not in frame:
                frame["content"] = text_content
            return {"type": str(event_type), "data": frame}

        if text_content or data_dict:
            return {
                "type": "thought",
                "data": {"content": text_content, **data_dict},
            }
        return None

    @staticmethod
    def _normalize_status(event: TaskStatusUpdateEvent) -> dict[str, Any] | None:
        if not event.status:
            return None

        content = EventNormalizer.extract_status_content(event)
        data: dict[str, Any] = {}
        if event.status.message:
            for part in event.status.message.parts:
                if part.WhichOneof("content") == "data":
                    maybe_data = MessageToDict(part.data)
                    if isinstance(maybe_data, dict):
                        data = maybe_data
                    break
        if event.HasField("metadata"):
            meta = MessageToDict(event.metadata)
            cascade_result = meta.get("cascade_result")
            if isinstance(cascade_result, dict):
                data = cascade_result

        state = event.status.state
        if state == TASK_STATE_FAILED:
            return {"type": "failed", "data": {"content": content, "error": content}}
        if state == TASK_STATE_INPUT_REQUIRED:
            return {"type": "input_required", "data": {"content": content}}
        return None

    @staticmethod
    def extract_status_content(event: TaskStatusUpdateEvent) -> str:
        if not event.status or not event.status.message:
            return ""
        for part in event.status.message.parts:
            kind = part.WhichOneof("content")
            if kind == "text":
                return part.text or ""
            if kind == "data":
                data = MessageToDict(part.data)
                return json.dumps(data, ensure_ascii=False, sort_keys=True) if isinstance(data, dict) else ""
        return ""
