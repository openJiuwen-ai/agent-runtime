from __future__ import annotations

import contextvars
import inspect
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import Request
from loguru import logger

# 日志规范固定分隔符（Ctrl+A）
_TAG_SEPARATOR = "\x01"
_TAG_HTTP_REQUEST_START = "TAG_HTTP_REQUEST_START"
_TAG_HTTP_REQUEST_END = "TAG_HTTP_REQUEST_END"
_TAG_VERSATILE_START = "TAG_VERSATILE_START"
_TAG_VERSATILE_END = "TAG_VERSATILE_END"
_DEFAULT_HTTP_WARN_THRESHOLD_MS = 3000
_DEFAULT_VERSATILE_WARN_THRESHOLD_MS = 2000
_MAX_LOG_STRING_LENGTH = 1024
_MAX_LOG_LIST_ITEMS = 20
_SENSITIVE_KEYWORDS = (
    "authorization",
    "token",
    "cookie",
    "password",
    "secret",
    "key",
    "phone",
    "mobile",
)

# Tag 名称按规范固定，不允许在业务侧动态传入或修改

# 共享调用链字段上下文（跨 HTTP/Versatile 共用）
_TRACE_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_log_helper_trace_id", default="unknown"
)
_AGENT_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_log_helper_agent_id", default="unknown"
)
_CONVERSATION_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "applications_log_helper_conversation_id", default="unknown"
)


@dataclass(frozen=True)
class LogContext:
    trace_id: str
    agent_id: str
    conversation_id: str


@dataclass(frozen=True)
class HttpRequestTagContext:
    log_context: LogContext
    request_path: str
    content_type: str
    request_headers: dict[str, str]
    request_body_snapshot: Any
    user_id: str


class LogHelper:
    @staticmethod
    def _positive_int(value: object, default: int) -> int:
        # 统一解析正整数配置，异常或非法值回退默认值
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def current_local_time() -> str:
        # 生成毫秒精度本地时间字符串
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        # 判断字段名是否命中敏感关键字
        key_lower = key.lower()
        return any(keyword in key_lower for keyword in _SENSITIVE_KEYWORDS)

    @staticmethod
    def sanitize_payload(value: Any, key: str = "") -> Any:
        # 对日志 payload 做递归脱敏和裁剪，控制安全与体积
        # 递归脱敏并对超长字段做截断，避免日志泄露和膨胀
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for item_key, item_value in value.items():
                if LogHelper._is_sensitive_key(item_key):
                    sanitized[item_key] = "***"
                else:
                    sanitized[item_key] = LogHelper.sanitize_payload(item_value, item_key)
            return sanitized

        if isinstance(value, list):
            result = [
                LogHelper.sanitize_payload(item, key)
                for item in value[:_MAX_LOG_LIST_ITEMS]
            ]
            if len(value) > _MAX_LOG_LIST_ITEMS:
                result.append(f"...truncated({len(value) - _MAX_LOG_LIST_ITEMS} items)")
            return result

        if isinstance(value, str):
            if LogHelper._is_sensitive_key(key):
                return "***"
            if len(value) > _MAX_LOG_STRING_LENGTH:
                return value[:_MAX_LOG_STRING_LENGTH] + "...(truncated)"
            return value

        return value

    @staticmethod
    def caller_location(depth: int = 3) -> str:
        # 向上回溯调用栈，定位真实业务调用位置
        frame = inspect.currentframe()
        try:
            steps = depth
            while frame and steps > 0:
                frame = frame.f_back
                steps -= 1
            if not frame:
                return "unknown:unknown:0"
            module_name = frame.f_globals.get("__name__", "unknown")
            return f"{module_name}:{frame.f_code.co_name}:{frame.f_lineno}"
        finally:
            del frame

    @staticmethod
    def extract_header_value(headers: dict[str, str], name: str) -> str:
        # 以大小写不敏感方式读取指定请求头
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return ""

    @staticmethod
    def current_tag_context() -> tuple[str, str, str]:
        # 读取当前上下文中的 trace/agent/conversation 三元组
        return _TRACE_ID_CTX.get(), _AGENT_ID_CTX.get(), _CONVERSATION_ID_CTX.get()

    @staticmethod
    def build_log_context(trace_id: str, agent_id: str, conversation_id: str) -> LogContext:
        # 构建标准日志上下文，并兜底 unknown 值
        return LogContext(
            trace_id=trace_id or "unknown",
            agent_id=agent_id or "unknown",
            conversation_id=conversation_id or "unknown",
        )

    @staticmethod
    @contextmanager
    def bind_context(log_context: LogContext):
        # 将日志上下文字段绑定到 contextvars 和 loguru contextualize
        trace_token = _TRACE_ID_CTX.set(log_context.trace_id)
        agent_token = _AGENT_ID_CTX.set(log_context.agent_id)
        conversation_token = _CONVERSATION_ID_CTX.set(log_context.conversation_id)
        try:
            with logger.contextualize(
                trace_id=log_context.trace_id,
                agent_id=log_context.agent_id,
                conversation_id=log_context.conversation_id,
            ):
                yield
        finally:
            _TRACE_ID_CTX.reset(trace_token)
            _AGENT_ID_CTX.reset(agent_token)
            _CONVERSATION_ID_CTX.reset(conversation_token)

    @staticmethod
    def resolve_http_warn_threshold_ms(settings: Any) -> int:
        # 读取 HTTP 慢请求阈值配置，缺省回退默认值
        return LogHelper._positive_int(
            getattr(settings, "tag_http_warn_threshold_ms", None),
            _DEFAULT_HTTP_WARN_THRESHOLD_MS,
        )

    @staticmethod
    def resolve_versatile_warn_threshold_ms() -> int:
        # 读取 Versatile 慢调用阈值配置，来源为环境变量
        return LogHelper._positive_int(
            os.getenv("TAG_VERSATILE_WARN_THRESHOLD_MS"),
            _DEFAULT_VERSATILE_WARN_THRESHOLD_MS,
        )

    @staticmethod
    def select_tag_level_by_duration(duration_ms: int, threshold_ms: int) -> str:
        # 根据耗时与阈值输出 INFO/WARN 级别
        if duration_ms > threshold_ms:
            return "WARN"
        return "INFO"

    @staticmethod
    def _emit_tag_log(
        *,
        level: str,
        event_tag: str,
        duration_ms: int,
        payload: dict[str, Any],
    ) -> None:
        # 按规范拼接 Tag 日志行并按等级输出
        trace_id, agent_id, conversation_id = LogHelper.current_tag_context()
        code_location = LogHelper.caller_location(depth=3)
        safe_payload = LogHelper.sanitize_payload(payload)
        line = _TAG_SEPARATOR.join(
            [
                LogHelper.current_local_time(),
                level,
                code_location,
                trace_id,
                agent_id,
                conversation_id,
                event_tag,
                str(max(duration_ms, 0)),
                json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
            ]
        )

        if level == "ERROR":
            logger.opt(raw=True).error(f"{line}\n")
        elif level == "WARN":
            logger.opt(raw=True).warning(f"{line}\n")
        else:
            logger.opt(raw=True).info(f"{line}\n")

    @staticmethod
    async def build_http_request_tag_context(
        *,
        request: Request,
        trace_id: str,
        agent_id: str,
        conversation_id: str,
    ) -> HttpRequestTagContext:
        # 从 FastAPI Request 提取头、体、用户标识并构建上下文对象
        # 在工具内部完成请求快照解析，业务侧仅传 request 和主键字段
        request_headers = dict(request.headers)
        content_type = request.headers.get("content-type", "").lower()
        request_body_snapshot: Any = {"raw_body": ""}

        try:
            raw_body = await request.body()
            raw_body_text = raw_body.decode("utf-8", errors="replace")
            if raw_body_text:
                if "application/json" in content_type:
                    try:
                        request_body_snapshot = json.loads(raw_body_text)
                    except Exception:
                        request_body_snapshot = {"raw_body": raw_body_text}
                else:
                    request_body_snapshot = {"raw_body": raw_body_text}
        except Exception:
            request_body_snapshot = {"raw_body": "<unavailable>"}

        user_id = LogHelper.extract_header_value(
            request_headers, "x-user-id"
        ) or LogHelper.extract_header_value(request_headers, "cust-userid")

        return HttpRequestTagContext(
            log_context=LogHelper.build_log_context(trace_id, agent_id, conversation_id),
            request_path=request.url.path,
            content_type=content_type,
            request_headers=request_headers,
            request_body_snapshot=request_body_snapshot,
            user_id=user_id,
        )

    @staticmethod
    def _build_http_ext_message(
        *,
        http_request_tag_context: HttpRequestTagContext,
        input_payload: Optional[dict[str, Any]] = None,
        output_payload: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
        release: str = "1.0.0",
    ) -> dict[str, Any]:
        # 组装 HTTP Start/End 的扩展消息结构
        trace_id = http_request_tag_context.log_context.trace_id
        return {
            "id": trace_id,
            "timestamp": LogHelper.current_local_time(),
            "name": http_request_tag_context.request_path,
            "user_id": http_request_tag_context.user_id,
            "session_id": http_request_tag_context.log_context.conversation_id,
            "input": input_payload if input_payload is not None else {},
            "output": output_payload if output_payload is not None else {},
            "metadata": metadata if metadata is not None else {"UNION_NO": trace_id},
            "tags": tags if tags is not None else [],
            "release": release,
        }

    @staticmethod
    def emit_http_request_start_tag(
        *,
        http_request_tag_context: HttpRequestTagContext,
        release: str = "1.0.0",
    ) -> None:
        # 记录入口请求开始 Tag，携带请求头和请求体快照
        LogHelper._emit_tag_log(
            level="INFO",
            event_tag=_TAG_HTTP_REQUEST_START,
            duration_ms=0,
            payload=LogHelper._build_http_ext_message(
                http_request_tag_context=http_request_tag_context,
                input_payload={
                    "request_header": http_request_tag_context.request_headers,
                    "request_body": http_request_tag_context.request_body_snapshot,
                },
                release=release,
            ),
        )

    @staticmethod
    def emit_http_request_end_tag(
        *,
        http_request_tag_context: HttpRequestTagContext,
        output_payload: dict[str, Any],
        duration_ms: int,
        warn_threshold_ms: int,
        level_override: Optional[str] = None,
        release: str = "1.0.0",
    ) -> None:
        # 记录入口请求结束 Tag，携带响应摘要和耗时信息
        level = level_override or LogHelper.select_tag_level_by_duration(
            duration_ms, warn_threshold_ms
        )
        LogHelper._emit_tag_log(
            level=level,
            event_tag=_TAG_HTTP_REQUEST_END,
            duration_ms=duration_ms,
            payload=LogHelper._build_http_ext_message(
                http_request_tag_context=http_request_tag_context,
                output_payload=output_payload,
                release=release,
            ),
        )

    def build_versatile_start_ext_message(
        *,
        call_id: str,
        name: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        # 组装 Versatile 开始日志扩展消息
        trace_id, _, _ = LogHelper.current_tag_context()
        return {
            "id": call_id,
            "trace_id": trace_id,
            "type": "TOOL",
            "name": name,
            "start_time": LogHelper.current_local_time(),
            "input": input_payload,
        }

    @staticmethod
    def build_versatile_end_ext_message(
        *,
        call_id: str,
        name: str,
        output_payload: dict[str, Any],
        status_message: Any,
        total_cost: int,
    ) -> dict[str, Any]:
        # 组装 Versatile 结束日志扩展消息
        trace_id, _, _ = LogHelper.current_tag_context()
        return {
            "id": call_id,
            "trace_id": trace_id,
            "type": "TOOL",
            "name": name,
            "end_time": LogHelper.current_local_time(),
            "output": output_payload,
            "status_message": status_message,
            "total_cost": total_cost,
        }

    @staticmethod
    def emit_versatile_start_tag(
        *,
        call_id: str,
        name: str,
        request_headers: dict[str, Any],
        request_body: Any,
    ) -> None:
        # 记录 Versatile 调用开始 Tag
        LogHelper._emit_tag_log(
            level="INFO",
            event_tag=_TAG_VERSATILE_START,
            duration_ms=0,
            payload=LogHelper.build_versatile_start_ext_message(
                call_id=call_id,
                name=name,
                input_payload={
                    "request_header": request_headers,
                    "request_body": request_body,
                },
            ),
        )

    @staticmethod
    def emit_versatile_end_tag(
        *,
        call_id: str,
        name: str,
        output_payload: dict[str, Any],
        status_message: Any,
        duration_ms: int,
        warn_threshold_ms: int,
        level_override: Optional[str] = None,
    ) -> None:
        # 记录 Versatile 调用结束 Tag，输出状态、耗时与返回摘要
        LogHelper._emit_tag_log(
            level=level_override
            or LogHelper.select_tag_level_by_duration(duration_ms, warn_threshold_ms),
            event_tag=_TAG_VERSATILE_END,
            duration_ms=duration_ms,
            payload=LogHelper.build_versatile_end_ext_message(
                call_id=call_id,
                name=name,
                output_payload=output_payload,
                status_message=status_message,
                total_cost=duration_ms,
            ),
        )
