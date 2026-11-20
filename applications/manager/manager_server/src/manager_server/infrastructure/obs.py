"""对象存储（MinIO / OBS）基础客户端。

凭据来自 ``settings.OBS_*``；SDK 惰性导入，未配置或不可达时只影响调用方，不拖垮管理面启动。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import SplitResult, urlsplit

from manager_server.infrastructure.config import settings

_SSL_VERIFY_OFF = {"", "0", "false", "no", "off"}
_DEFAULT_GET_MAX_BYTES = 2 * 1024 * 1024


class ObsError(Exception):
    """对象存储基础错误。"""


class ObsNotConfiguredError(ObsError):
    """OBS_* 凭据或地址未配齐。"""


class ObsEndpointInvalidError(ObsError):
    """OBS_URL 非法。"""


class ObsUnavailableError(ObsError):
    """对象存储不可达或返回非业务错误。"""


class ObsObjectError(ObsError):
    """对象级失败（权限、超限等；不含 NotFound）。"""


def _obs_http_client() -> Any:
    from urllib3 import PoolManager, Timeout

    # 默认校验证书（安全默认）；仅显式 false/0/off/no/空串时关闭（如内置 MinIO 自签或纯 HTTP）
    verify = os.getenv("JIUWENSWARM_SSL_VERIFY", "true").strip().lower() not in _SSL_VERIFY_OFF
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


def get_client() -> tuple[Any, str]:
    """返回 ``(Minio client, bucket)``；未配置或 endpoint 非法时抛 ``ObsError`` 子类。"""
    from minio import Minio

    endpoint = str(settings.obs_url or "").strip()
    access = str(settings.obs_access_key or "").strip()
    secret = str(settings.obs_secret_key or "").strip()
    bucket = str(settings.obs_bucket or "").strip()
    if not all((endpoint, access, secret, bucket)):
        raise ObsNotConfiguredError("object storage is not configured")
    secure = str(settings.obs_secure or "").strip().lower() in {"true", "1", "yes"}
    parsed = urlsplit(
        endpoint if "://" in endpoint else f"{'https' if secure else 'http'}://{endpoint}"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
    ):
        raise ObsEndpointInvalidError("object storage endpoint is invalid")
    try:
        authority, virtual_host = _obs_authority(parsed, bucket)
    except ValueError as exc:
        raise ObsEndpointInvalidError("object storage endpoint is invalid") from exc
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


def fetch_object_bytes_sync(
    object_key: str, *, max_bytes: int = _DEFAULT_GET_MAX_BYTES
) -> bytes:
    """直读对象字节；对象不存在抛 ``FileNotFoundError``。"""
    from minio.error import S3Error

    key = str(object_key or "").strip().lstrip("/")
    if not key:
        raise ValueError("object_key is empty")
    client, bucket = get_client()
    try:
        response = client.get_object(bucket, key)
        try:
            data = response.read(max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
    except FileNotFoundError:
        raise
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise FileNotFoundError(f"object not found: {key}") from exc
        raise ObsObjectError(
            f"object storage returned {exc.code or 'an error'}"
        ) from exc
    except ObsError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ObsUnavailableError("object storage is unreachable") from exc

    if len(data) > max_bytes:
        raise ObsObjectError("object is larger than the allowed limit")
    return data


async def fetch_object_bytes(
    object_key: str, *, max_bytes: int = _DEFAULT_GET_MAX_BYTES
) -> bytes:
    return await asyncio.to_thread(
        fetch_object_bytes_sync, object_key, max_bytes=max_bytes
    )
