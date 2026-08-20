# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""统一消息结构（Envelope）。

设计 §5。字段名对齐现有 Message / E2AEnvelope / IRequest，使业务层 normalize 极轻。
序列化为 JSON：每个结构提供 ``to_dict`` / ``from_dict``，``metadata.timestamp`` 为
float（直接 JSON 可序列化）；``rawdata`` 为任意 JSON 可序列化 dict。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel


TRequest = TypeVar("TRequest")


@dataclass
class Metadata:
    """请求元数据。``request_id`` 必填（幂等键 + 链路追踪键）；``extra`` 可扩展不破坏 schema。"""

    request_id: str
    user_id: str | None = None
    chat_id: str | None = None
    session_id: str | None = None
    bot_id: str | None = None
    channel: str | None = None
    timestamp: float | None = None
    trace_id: str | None = None
    instance_id: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "session_id": self.session_id,
            "bot_id": self.bot_id,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "instance_id": self.instance_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Metadata":
        return cls(
            request_id=d["request_id"],
            user_id=d.get("user_id"),
            chat_id=d.get("chat_id"),
            session_id=d.get("session_id"),
            bot_id=d.get("bot_id"),
            channel=d.get("channel"),
            timestamp=d.get("timestamp"),
            trace_id=d.get("trace_id"),
            instance_id=d.get("instance_id"),
            extra=dict(d.get("extra") or {}),
        )


@dataclass
class Envelope(Generic[TRequest]):
    """框架唯一入口消息结构。``type`` 既是路由键又是 REST 路径段。"""

    type: str
    metadata: Metadata
    rawdata: TRequest
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "metadata": self.metadata.to_dict(),
            "rawdata": (
                self.rawdata.model_dump(mode="json")
                if isinstance(self.rawdata, BaseModel)
                else self.rawdata
            ),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Envelope[dict[str, Any]]":
        return cls(
            type=d["type"],
            metadata=Metadata.from_dict(d.get("metadata") or {}),
            rawdata=dict(d.get("rawdata") or {}),
            version=d.get("version", "1"),
        )


@dataclass
class ResponseEnvelope:
    """统一响应信封（非流式）。失败一律 ``ok=False`` + error_code/error_message。

    ``retry_after``（秒，可选）：过载类错误（如限流/排队超时）建议调用方的重试间隔；
    其余错误省略（``None``，序列化时省略该字段）。
    """

    type: str
    metadata: Metadata
    rawdata: dict
    ok: bool
    error_code: str | None = None
    error_message: str | None = None
    retry_after: int | None = None
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        body = {
            "type": self.type,
            "metadata": self.metadata.to_dict(),
            "rawdata": self.rawdata,
            "ok": self.ok,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "version": self.version,
        }
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        return body

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResponseEnvelope":
        retry_after = d.get("retry_after")
        return cls(
            type=d["type"],
            metadata=Metadata.from_dict(d.get("metadata") or {}),
            rawdata=dict(d.get("rawdata") or {}),
            ok=bool(d.get("ok", False)),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            retry_after=int(retry_after) if retry_after is not None else None,
            version=d.get("version", "1"),
        )


@dataclass
class StreamChunk:
    """流式响应分片。``sequence`` 递增，末帧 ``is_final=True``。"""

    sequence: int
    is_final: bool
    metadata: Metadata
    rawdata: dict
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "is_final": self.is_final,
            "metadata": self.metadata.to_dict(),
            "rawdata": self.rawdata,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StreamChunk":
        return cls(
            sequence=int(d["sequence"]),
            is_final=bool(d.get("is_final", False)),
            metadata=Metadata.from_dict(d.get("metadata") or {}),
            rawdata=dict(d.get("rawdata") or {}),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
        )
