# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""把 foundation 校验英文句归类为可本地化错误码（管理面展示用）。

foundation ``config_contract`` 仍保留英文 ``message``（日志 / Runtime 联调）；
Manager 在预检失败时优先抽出稳定 ``code`` + 参数，前端按 TemplateInUse 同套路 i18n。
连带噪音（容器因校验失败被丢弃后的 “not present”）会被压掉，只保留根因。
"""

from __future__ import annotations

import ast
import re
from typing import Any

from manager_server.core.template.errors import TemplateValidationError

# foundation 文案稳定子串（改 foundation 句子时须同步这里）。
_DUP_ENV = re.compile(
    r"\.name ['\"](?P<name>[^'\"]+)['\"] duplicates an earlier env entry"
)
_VOL_UNUSED = re.compile(
    r"volumes not mounted by any container:\s*(?P<names>.+?)\s*$"
)
_VOL_UNDEF = re.compile(
    r"\.name ['\"](?P<name>[^'\"]+)['\"] is not defined in the template volumes"
)
_CASCADE_MARKERS = (
    "not present in the containers section",
    "not referenced by any template",
)


def _parse_name_list(raw: str) -> list[str]:
    text = raw.strip()
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        value = None
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
    return [text] if text else []


def _is_cascade(message: str) -> bool:
    text = message.strip()
    return any(marker in text for marker in _CASCADE_MARKERS)


def classify_message(message: str) -> TemplateValidationError | None:
    """单条英文校验句 → 结构化错误；无法识别则返回 None。

    ``message`` 使用中文操作建议：当前 Manager Web 对结构化 detail 会优先展示
    ``message`` 字段；前端也可再按 ``code`` 做 i18n。
    """
    text = (message or "").strip()
    if not text:
        return None
    match = _DUP_ENV.search(text)
    if match:
        name = match.group("name")
        return TemplateValidationError(
            "duplicate_env_name",
            f"环境变量「{name}」重复了，请只保留一项后再保存。",
            name=name,
        )
    match = _VOL_UNUSED.search(text)
    if match:
        names = _parse_name_list(match.group("names"))
        joined = ", ".join(names) if names else ""
        return TemplateValidationError(
            "volumes_not_mounted",
            (
                f"卷「{joined}」已声明但未被任何容器挂载。"
                "请在主容器或 Sandbox 的 volumeMounts 中按名称引用，或从 volumes 中删除。"
            ),
            names=names,
            name=joined,
        )
    match = _VOL_UNDEF.search(text)
    if match:
        name = match.group("name")
        return TemplateValidationError(
            "volume_mount_undefined",
            (
                f"挂载名「{name}」未在运行时模板的 volumes 中定义。"
                "请先在 volumes 中声明同名卷，或删掉该 volumeMount。"
            ),
            name=name,
        )
    return None


def raise_from_validation_errors(
    errors: list[dict[str, str]],
    *,
    template_id: str = "",
) -> None:
    """有错误则抛出；优先可本地化根因，并丢掉连锁 not-present 噪音。"""
    if not errors:
        return
    primary: list[TemplateValidationError] = []
    leftovers: list[str] = []
    for item in errors:
        path = str(item.get("path") or "").strip()
        message = str(item.get("message") or "").strip()
        blob = f"{path}: {message}" if path else message
        if _is_cascade(message):
            continue
        classified = classify_message(message)
        if classified is not None:
            primary.append(classified)
        elif blob:
            leftovers.append(blob)
    if primary:
        raise primary[0]
    tid = str(template_id or "").strip()
    detail = "; ".join(leftovers) if leftovers else "runtime config validation failed"
    prefix = f"runtime config validation failed template_id={tid}: " if tid else (
        "runtime config validation failed: "
    )
    raise ValueError(prefix + detail)


def maybe_classify_value_error(exc: ValueError) -> ValueError:
    """Runtime 透传英文句 / 旧预检长句 → 尽量升成 TemplateValidationError。"""
    if isinstance(exc, TemplateValidationError):
        return exc
    classified = classify_message(str(exc))
    return classified if classified is not None else exc


def validation_http_detail(exc: TemplateValidationError) -> dict[str, Any]:
    """FastAPI ``detail`` 对象，供前端按 code 本地化。"""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc),
    }
    detail.update(exc.fields)
    return detail
