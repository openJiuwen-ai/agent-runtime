"""企业版 Skill 按包上传审批：包描述符字段与时效校验。

描述符由浏览器转发，任意用户可构造；这里只做字段与对象键结构校验，
真正的可信锚点由读取时校验 OBS 元数据与内容 ``sha256`` 提供。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from manager_server.core.skill_upload.errors import SkillUploadValidationError

SCHEMA_VERSION = 1

_SKILL_NAME_MAX_LEN = 128
_DESCRIPTION_MAX_LEN = 1024
_VERSION_MAX_LEN = 64
_FILE_COUNT_MAX = 5000
_UNCOMPRESSED_MAX_BYTES = 100 * 1024 * 1024
_PACKAGE_MAX_BYTES = 50 * 1024 * 1024
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OBJECT_KEY_RE = re.compile(
    r"^skill-approvals/[0-9a-f]{64}/([0-9a-f]{32})/[^/\\\0]+$"
)


def _require_text(value: Any, *, field: str, max_len: int, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not allow_empty and not text:
        raise SkillUploadValidationError(f"{field} is required")
    if len(text) > max_len:
        raise SkillUploadValidationError(f"{field} is too long")
    return text


def _parse_time(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise SkillUploadValidationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SkillUploadValidationError(f"{field} is not ISO8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_skill_name(value: Any) -> str:
    name = _require_text(value, field="skill_name", max_len=_SKILL_NAME_MAX_LEN)
    bad_prefix = name[0] in {"_", "."}
    bad_segment = name in {".", ".."} or ".." in name
    bad_char = any(char in name for char in "/\\\0")
    if bad_prefix or bad_segment or bad_char:
        raise SkillUploadValidationError("skill_name is invalid")
    return name


def _validate_object_key(value: Any, *, upload_id: str) -> str:
    """对象键必须落在审批前缀内，且其中的 upload_id 与描述符一致。"""
    key = _require_text(
        value, field="package.object_key", max_len=1024
    )
    match = _OBJECT_KEY_RE.fullmatch(key)
    if match is None:
        raise SkillUploadValidationError("package.object_key is invalid")
    if match.group(1) != upload_id:
        raise SkillUploadValidationError(
            "package.object_key does not match upload_id"
        )
    return key


def validate_descriptor(descriptor: dict[str, Any]) -> None:
    """字段与时效约束。"""
    if not isinstance(descriptor, dict):
        raise SkillUploadValidationError("package_info must be an object")
    if descriptor.get("schema_version") != SCHEMA_VERSION:
        raise SkillUploadValidationError("unsupported descriptor schema_version")

    upload_id = _require_text(descriptor.get("upload_id"), field="upload_id", max_len=32)
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        raise SkillUploadValidationError("upload_id must be 32 lowercase hex chars")

    _validate_skill_name(descriptor.get("skill_name"))

    version = descriptor.get("skill_version")
    if version is not None and len(str(version)) > _VERSION_MAX_LEN:
        raise SkillUploadValidationError("skill_version is too long")

    description = descriptor.get("description")
    if description is not None and len(str(description)) > _DESCRIPTION_MAX_LEN:
        raise SkillUploadValidationError("description is too long")

    file_count = descriptor.get("file_count")
    if not isinstance(file_count, int) or isinstance(file_count, bool):
        raise SkillUploadValidationError("file_count must be an integer")
    if not 1 <= file_count <= _FILE_COUNT_MAX:
        raise SkillUploadValidationError("file_count is out of range")

    uncompressed = descriptor.get("uncompressed_bytes")
    if not isinstance(uncompressed, int) or isinstance(uncompressed, bool):
        raise SkillUploadValidationError("uncompressed_bytes must be an integer")
    if not 0 < uncompressed <= _UNCOMPRESSED_MAX_BYTES:
        raise SkillUploadValidationError("uncompressed_bytes is out of range")

    package = descriptor.get("package")
    if not isinstance(package, dict):
        raise SkillUploadValidationError("package is required")
    filename = _require_text(package.get("filename"), field="package.filename", max_len=255)
    if (
        filename in {".", ".."}
        or any(char in filename for char in "/\\\0")
        or ".." in filename
    ):
        raise SkillUploadValidationError("package.filename is invalid")
    size_bytes = package.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        raise SkillUploadValidationError("package.size_bytes must be an integer")
    if not 0 < size_bytes <= _PACKAGE_MAX_BYTES:
        raise SkillUploadValidationError("package.size_bytes is out of range")
    if not _DIGEST_RE.fullmatch(str(package.get("sha256") or "")):
        raise SkillUploadValidationError("package.sha256 is invalid")
    _validate_object_key(package.get("object_key"), upload_id=upload_id)

    if not _DIGEST_RE.fullmatch(str(descriptor.get("digest") or "")):
        raise SkillUploadValidationError("digest is invalid")

    staged_at = _parse_time(descriptor.get("staged_at"), field="staged_at")
    stage_expires_at = _parse_time(
        descriptor.get("stage_expires_at"), field="stage_expires_at"
    )
    now = datetime.now(timezone.utc)
    if stage_expires_at <= staged_at:
        raise SkillUploadValidationError("stage_expires_at must be after staged_at")
    if stage_expires_at <= now:
        raise SkillUploadValidationError(
            "staged package has expired; please upload again"
        )


def validate_package_info(package_info: dict[str, Any]) -> dict[str, Any]:
    """校验字段与时效，返回描述符副本。"""
    if not isinstance(package_info, dict):
        raise SkillUploadValidationError("package_info must be an object")
    validate_descriptor(package_info)
    return dict(package_info)
