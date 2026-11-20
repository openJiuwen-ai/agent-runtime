"""企业版 Skill 上传审批：在基础 OBS 客户端之上做包元数据 / sha256 校验。

通用连接与 ``GetObject`` 见 ``manager_server.infrastructure.obs``。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from manager_server.core.skill_upload.errors import (
    SkillUploadError,
    SkillUploadExpiredError,
    SkillUploadPackageUnavailableError,
    SkillUploadUnavailableError,
)
from manager_server.infrastructure import obs as obs_infra
from manager_server.infrastructure.config import settings

_PACKAGE_MAX_BYTES = 50 * 1024 * 1024


def _store() -> tuple[Any, str]:
    """技能包路径使用的客户端；将基础设施错误映射为 SkillUpload*。"""
    try:
        return obs_infra.get_client()
    except obs_infra.ObsNotConfiguredError as exc:
        raise SkillUploadUnavailableError("object storage is not configured") from exc
    except obs_infra.ObsEndpointInvalidError as exc:
        raise SkillUploadUnavailableError(
            "object storage endpoint is invalid"
        ) from exc


def _map_storage_error(exc: Any) -> SkillUploadError:
    if exc.code in {"NoSuchKey", "NoSuchObject"}:
        return SkillUploadExpiredError(
            "original package is no longer available; please upload again"
        )
    return SkillUploadPackageUnavailableError(
        f"object storage returned {exc.code or 'an error'}"
    )


def fetch_package_sync(object_key: str, expected_sha256: str) -> bytes:
    from minio.error import S3Error

    client, bucket = _store()
    try:
        stat = client.stat_object(bucket, object_key)
        metadata = {
            str(key).lower(): str(value)
            for key, value in (stat.metadata or {}).items()
        }
        if metadata.get("x-amz-meta-sha256") != expected_sha256:
            raise SkillUploadPackageUnavailableError(
                "package metadata does not match the declared digest"
            )
        response = client.get_object(bucket, object_key)
        try:
            data = response.read(_PACKAGE_MAX_BYTES + 1)
        finally:
            response.close()
            response.release_conn()
    except SkillUploadError:
        raise
    except S3Error as exc:
        raise _map_storage_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise SkillUploadPackageUnavailableError(
            "object storage is unreachable"
        ) from exc

    if not data:
        raise SkillUploadPackageUnavailableError("package is empty")
    if len(data) > _PACKAGE_MAX_BYTES:
        raise SkillUploadPackageUnavailableError(
            "package is larger than the allowed limit"
        )
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if digest != expected_sha256:
        raise SkillUploadPackageUnavailableError(
            "downloaded package does not match the declared digest"
        )
    return data


async def fetch_package(object_key: str, expected_sha256: str) -> bytes:
    """校验对象元数据与内容 sha256 后返回原始 ZIP。"""
    return await asyncio.to_thread(fetch_package_sync, object_key, expected_sha256)
