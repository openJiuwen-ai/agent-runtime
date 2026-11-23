#!/usr/bin/env python3
"""Upload/delete an object on S3-compatible storage (MinIO / OBS).

Stdlib only (no ``minio`` / ``boto3``), so deploy hosts that only ship curl/yq/python3
can still publish ``agentserver.json`` for manager 「导入默认配置」.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class ObsHttpError(RuntimeError):
    """带 HTTP 状态码的 OBS 请求失败（避免仅靠错误文案子串判断）。"""

    def __init__(self, method: str, status: int, detail: str = "") -> None:
        self.method = method
        self.status = int(status)
        self.detail = detail
        msg = f"OBS {method} failed: HTTP {self.status}"
        if detail:
            msg = f"{msg} {detail}"
        super().__init__(msg)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _split_endpoint(endpoint: str, secure: bool) -> tuple[str, str, bool]:
    raw = endpoint.strip()
    if "://" not in raw:
        raw = ("https://" if secure else "http://") + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid endpoint: {endpoint}")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"endpoint must not include a path: {endpoint}")
    use_https = parsed.scheme == "https"
    return parsed.scheme, parsed.netloc, use_https


def _request(
    *,
    method: str,
    endpoint: str,
    secure: bool,
    bucket: str,
    object_key: str = "",
    access_key: str,
    secret_key: str,
    region: str,
    body: bytes = b"",
    content_type: str = "application/octet-stream",
) -> int:
    scheme, netloc, _ = _split_endpoint(endpoint, secure)
    key = object_key.lstrip("/")
    # path-style: http://host/bucket[/key...]；建桶 / HeadBucket 时无 object key
    if key:
        canonical_uri = "/" + bucket + "/" + "/".join(
            urllib.parse.quote(part, safe="-_.~") for part in key.split("/")
        )
    else:
        canonical_uri = "/" + urllib.parse.quote(bucket, safe="-_.~")
    url = f"{scheme}://{netloc}{canonical_uri}"

    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = _sha256_hex(body)
    host = netloc
    content_length = str(len(body))

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if method in {"PUT", "POST"}:
        headers["content-type"] = content_type
        headers["content-length"] = content_length

    signed_header_items = sorted(headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in signed_header_items)
    signed_headers = ";".join(k for k, _ in signed_header_items)
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, datestamp, region, "s3"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    req_headers = {
        "Host": host,
        "X-Amz-Content-Sha256": payload_hash,
        "X-Amz-Date": amz_date,
        "Authorization": authorization,
    }
    if method in {"PUT", "POST"}:
        req_headers["Content-Type"] = content_type
        req_headers["Content-Length"] = content_length

    data = body if method not in {"GET", "HEAD", "DELETE"} else None
    request = urllib.request.Request(url, data=data, method=method)
    for hk, hv in req_headers.items():
        request.add_header(hk, hv)

    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            if status >= 400:
                raise ObsHttpError(method, status)
            return status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ObsHttpError(method, int(exc.code), detail) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OBS {method} unreachable: {exc}") from exc


def _ensure_bucket(
    *,
    endpoint: str,
    secure: bool,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> None:
    """桶不存在则创建；HeadBucket 无权限时跳过，交由后续对象 PUT 决定成败。"""
    common = dict(
        endpoint=endpoint,
        secure=secure,
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
    )
    try:
        _request(method="HEAD", object_key="", **common)
        return
    except ObsHttpError as exc:
        # 最小权限 AK 常无 HeadBucket：403 时不阻断，避免桶已存在却上传失败
        if exc.status in {401, 403}:
            # 桶可能已存在但 AK 无 HeadBucket：不阻断，交由后续对象 PUT 判定
            logger.warning(
                "HeadBucket 鉴权不足，跳过建桶检查: s3://%s (HTTP %s)",
                bucket,
                exc.status,
            )
            return
        if exc.status != 404 and "NoSuchBucket" not in exc.detail:
            raise
    try:
        _request(method="PUT", object_key="", body=b"", **common)
        logger.info("created OBS bucket s3://%s", bucket)
    except ObsHttpError as exc:
        # 并发建桶竞态：视为已存在
        already_exists = (
            exc.status == 409
            or "BucketAlreadyOwnedByYou" in exc.detail
            or "BucketAlreadyExists" in exc.detail
        )
        if already_exists:
            return
        # 无建桶权限：交由对象 PUT；桶真缺失时 PUT 会再报错
        if exc.status in {401, 403}:
            logger.warning(
                "CreateBucket 鉴权不足，跳过建桶: s3://%s (HTTP %s)",
                bucket,
                exc.status,
            )
            return
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="OBS/MinIO host or URL")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--access-key", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--region", default="default")
    parser.add_argument(
        "--secure",
        default="false",
        help="true/false; used when --endpoint has no scheme",
    )
    parser.add_argument("--object-key", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="local file to upload (PUT)")
    group.add_argument("--delete", action="store_true", help="delete object")
    args = parser.parse_args()

    secure = str(args.secure).strip().lower() in {"1", "true", "yes", "on"}
    try:
        if args.delete:
            _request(
                method="DELETE",
                endpoint=args.endpoint,
                secure=secure,
                bucket=args.bucket,
                object_key=args.object_key,
                access_key=args.access_key,
                secret_key=args.secret_key,
                region=args.region or "default",
            )
            logger.info(
                "deleted s3://%s/%s",
                args.bucket,
                args.object_key.lstrip("/"),
            )
            return 0

        path = Path(args.file)
        if not path.is_file():
            logger.error("file not found: %s", path)
            return 1
        body = path.read_bytes()
        _ensure_bucket(
            endpoint=args.endpoint,
            secure=secure,
            bucket=args.bucket,
            access_key=args.access_key,
            secret_key=args.secret_key,
            region=args.region or "default",
        )
        _request(
            method="PUT",
            endpoint=args.endpoint,
            secure=secure,
            bucket=args.bucket,
            object_key=args.object_key,
            access_key=args.access_key,
            secret_key=args.secret_key,
            region=args.region or "default",
            body=body,
            content_type="application/json",
        )
        logger.info(
            "uploaded %s -> s3://%s/%s (%d bytes)",
            path,
            args.bucket,
            args.object_key.lstrip("/"),
            len(body),
        )
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
