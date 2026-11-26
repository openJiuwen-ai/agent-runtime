"""高级配置模板五开关 + data 校验（与 Gateway 副本规则一致）。"""

from __future__ import annotations

from typing import Any

ADVANCED_CONFIG_BOOL_FIELDS: tuple[str, ...] = (
    "progressive_tool_enabled",
    "progressive_tool_optimize",
    "a4p_enabled",
    "auto_memory_enabled",
    "enable_kv_cache_affinity",
)

DEFAULT_ADVANCED_CONFIG_SWITCHES: dict[str, bool] = {
    "progressive_tool_enabled": True,
    "progressive_tool_optimize": False,
    "a4p_enabled": False,
    "auto_memory_enabled": False,
    "enable_kv_cache_affinity": False,
}


def validate_advanced_config_data(data: Any) -> dict[str, Any] | None:
    """``data`` 须为 null 或 object。"""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("advanced_config_template.data must be a JSON object or null")
    return data


def normalize_advanced_config_switches(
    payload: dict[str, Any],
    *,
    require_all: bool = False,
) -> dict[str, bool]:
    """校验并规范化五布尔开关。

    ``require_all=True`` 时五键都必须出现（create）；否则仅规范化已出现的键（update）。
    """
    out: dict[str, bool] = {}
    for key in ADVANCED_CONFIG_BOOL_FIELDS:
        if key not in payload:
            if require_all:
                raise ValueError(f"advanced_config_template missing required field {key!r}")
            continue
        value = payload[key]
        if not isinstance(value, bool):
            raise ValueError(f"advanced_config_template.{key} must be a boolean")
        out[key] = value
    unknown = [
        key
        for key in payload
        if key not in ADVANCED_CONFIG_BOOL_FIELDS and key != "data"
    ]
    # 调用方传入的可能是整行 updates；未知业务键由 schema extra=ignore 处理。
    # 这里只拒绝显式塞进「开关字典」的非法键。
    _ = unknown
    return out


__all__ = (
    "ADVANCED_CONFIG_BOOL_FIELDS",
    "DEFAULT_ADVANCED_CONFIG_SWITCHES",
    "normalize_advanced_config_switches",
    "validate_advanced_config_data",
)
