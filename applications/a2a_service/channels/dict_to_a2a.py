from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from a2a.types.a2a_pb2 import Artifact, Part, TaskArtifactUpdateEvent
from google.protobuf.struct_pb2 import Struct, Value

from common.logger import Level
from .observability import log_channel_event, record_dict_to_a2a_validation_error


# 按 (task_id, event_type) 缓存 artifact_id，使同类流式事件追加到同一 artifact。
# 避免每个事件都创建新 artifact 导致 Task 体积线性膨胀、save 代价 O(N²) 雪崩。
# 单个 run_agent 是单协程顺序处理事件流，同一 (task_id, event_type) 不会并发访问。
_ARTIFACT_ID_CACHE: Dict[str, str] = {}


def _resolve_artifact_id(task_id: str, event_type: str) -> tuple[str, bool]:
    """返回 (artifact_id, append)。

    首次访问生成新 UUID 并缓存，append=False 让 SDK 创建 artifact；
    后续访问复用同一 UUID，append=True 让 SDK 追加 parts 到同一 artifact。
    """
    cache_key = f"{task_id}:{event_type}"
    artifact_id = _ARTIFACT_ID_CACHE.get(cache_key)
    if artifact_id is None:
        artifact_id = str(uuid.uuid4())
        _ARTIFACT_ID_CACHE[cache_key] = artifact_id
        return artifact_id, False
    return artifact_id, True


def clear_artifact_id_cache(task_id: str) -> None:
    """清理指定 task_id 相关的 artifact_id 缓存。

    在 Task 完成/取消/失败时调用，避免缓存随 task 数线性增长。
    """
    prefix = f"{task_id}:"
    keys_to_remove = [k for k in _ARTIFACT_ID_CACHE if k.startswith(prefix)]
    for k in keys_to_remove:
        del _ARTIFACT_ID_CACHE[k]


def _build_data_part(data: dict[str, Any]) -> Part:
    struct = Struct()
    struct.update(data)
    value = Value()
    value.struct_value.CopyFrom(struct)
    part = Part()
    part.data.CopyFrom(value)
    return part


def dict_to_a2a(
    event: dict[str, Any],
    task_id: str,
    conv_id: str,
    artifact_id: Optional[str] = None,
) -> TaskArtifactUpdateEvent:
    if not isinstance(event, dict):
        error = TypeError("event must be a dict")
        record_dict_to_a2a_validation_error(
            event_type="<unknown>", task_id=task_id, conversation_id=conv_id, error=error
        )
        raise error
    event_type = event.get("type")
    if not event_type:
        error = ValueError("event dict must include type")
        record_dict_to_a2a_validation_error(
            event_type="<missing>", task_id=task_id, conversation_id=conv_id, error=error
        )
        raise error
    data = event.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        error = TypeError("event data must be a dict")
        record_dict_to_a2a_validation_error(
            event_type=str(event_type), task_id=task_id, conversation_id=conv_id, error=error
        )
        raise error

    frame = {"type": event_type, **data}
    log_channel_event(
        level=Level.DEBUG,
        action="DICT_TO_A2A_CONVERT",
        event_type=str(event_type),
        task_id=task_id,
        conversation_id=conv_id,
        payload={"data_keys": sorted(data.keys())},
    )
    # 默认按 (task_id, event_type) 复用 artifact_id，将同类流式事件合并为单个 artifact，
    # 使 artifacts 数量从 O(事件数) 降为 O(事件类型数)。
    # 显式传入 artifact_id 时（如测试或需要隔离的场景）直接使用，不参与缓存且 append=False。
    if artifact_id is None:
        artifact_id, append = _resolve_artifact_id(task_id, str(event_type))
    else:
        append = False
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[_build_data_part(frame)],
    )
    artifact.metadata.update({"type": event_type})
    return TaskArtifactUpdateEvent(
        task_id=task_id,
        context_id=conv_id,
        artifact=artifact,
        append=append,
        last_chunk=False,
    )
