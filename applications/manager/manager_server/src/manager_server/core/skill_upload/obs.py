"""企业版 Skill 上传审批：manager 只读访问 OBS 中的原始包。

凭据复用 AgentServer 的 AK/SK；本模块只调用 ``StatObject`` / ``GetObject``。
SDK 采用惰性导入：对象存储不可用时只影响下载接口，不应拖垮管理面启动。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any
from urllib.parse import SplitResult, urlsplit

from manager_server.core.skill_upload.errors import (
    SkillUploadError,
    SkillUploadExpiredError,
    SkillUploadPackageUnavailableError,
    SkillUploadUnavailableError,
)
from manager_server.infrastructure.config import settings

_PACKAGE_MAX_BYTES = 50 * 1024 * 1024
_SSL_VERIFY_OFF = {"", "0", "false", "no", "off"}


def _obs_http_client() -> Any:
    from urllib3 import PoolManager, Timeout

    verify = os.getenv("JIUWENSWARM_SSL_VERIFY", "false").strip().lower() not in _SSL_VERIFY_OFF
    return PoolManager(
        timeout=Timeout(connect=5, read=60),
        retries=False,
        cert_reqs="CERT_REQUIRED" if verify else "CERT_NONE",
    )


def _obs_authority(parsed: SplitResult, bucket: str) -> tuple[str, bool]:
    """Return the SDK endpoint and whether virtual-host addressing is required.

    Mirrors ``AttachmentStore.from_env()`` in AgentServer so manager and
    agentserver sign requests against Huawei OBS the same way.
    """
    regional_host = (parsed.hostname or "").rstrip(".")
    # A bucket-prefixed OBS setting still uses the regional endpoint in MinIO so
    # it adds the bucket exactly once when signing requests.
    if regional_host.startswith(f"{bucket}.obs."):
        regional_host = regional_host.removeprefix(f"{bucket}.")
    if not (
        regional_host.startswith("obs.")
        and ".myhuaweicloud." in regional_host
    ):
        return parsed.netloc, False
    if parsed.username is not None:
        raise ValueError("OBS_URL must not contain user information")
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port
    if port not in (None, default_port):
        return f"{regional_host}:{port}", True
    return regional_host, True


def _store() -> tuple[Any, str]:
    from minio import Minio

    endpoint = str(settings.obs_url or "").strip()
    access = str(settings.obs_access_key or "").strip()
    secret = str(settings.obs_secret_key or "").strip()
    bucket = str(settings.obs_bucket or "").strip()
    if not all((endpoint, access, secret, bucket)):
        raise SkillUploadUnavailableError("object storage is not configured")
    secure = str(settings.obs_secure or "").strip().lower() in {"true", "1", "yes"}
    parsed = urlsplit(
        endpoint if "://" in endpoint else f"{'https' if secure else 'http'}://{endpoint}"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
    ):
        raise SkillUploadUnavailableError("object storage endpoint is invalid")
    try:
        authority, virtual_host = _obs_authority(parsed, bucket)
    except ValueError as exc:
        raise SkillUploadUnavailableError(
            "object storage endpoint is invalid"
        ) from exc
    client = Minio(
        authority,
        access_key=access,
        secret_key=secret,
        secure=parsed.scheme == "https",
        region=str(settings.obs_region or "default"),
        http_client=_obs_http_client(),
    )
    if virtual_host:
        client.enable_virtual_style_endpoint()
    return client, bucket


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
