# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""manager 直读 OBS：元数据锚点、内容摘要与错误映射。"""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest
from minio.error import S3Error

from manager_server.core.skill_upload import obs
from manager_server.core.skill_upload.errors import (
    SkillUploadExpiredError,
    SkillUploadPackageUnavailableError,
)

pytestmark = pytest.mark.unit

OBJECT_KEY = f"skill-approvals/{'a' * 64}/{'b' * 32}/demo.zip"


class _FakeResponse(io.BytesIO):
    def release_conn(self) -> None:
        pass


class _FakeClient:
    def __init__(
        self,
        *,
        data: bytes = b"",
        metadata: dict | None = None,
        stat_error: S3Error | None = None,
    ) -> None:
        self.data = data
        self.metadata = metadata if metadata is not None else {}
        self.stat_error = stat_error
        self.get_calls = 0

    def stat_object(self, bucket: str, key: str) -> SimpleNamespace:
        _ = bucket, key
        if self.stat_error is not None:
            raise self.stat_error
        return SimpleNamespace(metadata=self.metadata)

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        _ = bucket, key
        self.get_calls += 1
        return _FakeResponse(self.data)


def _s3_error(code: str) -> S3Error:
    return S3Error(
        response=None,
        code=code,
        message=code,
        resource="",
        request_id="",
        host_id="",
    )


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _use_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(obs, "_store", lambda: (client, "bucket"))


def test_fetch_verifies_metadata_and_content(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"zip-payload"
    client = _FakeClient(
        data=data, metadata={"x-amz-meta-sha256": _digest(data)}
    )
    _use_client(monkeypatch, client)

    assert obs.fetch_package_sync(OBJECT_KEY, _digest(data)) == data


def test_fetch_rejects_metadata_mismatch_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"zip-payload"
    client = _FakeClient(
        data=data, metadata={"x-amz-meta-sha256": _digest(b"other")}
    )
    _use_client(monkeypatch, client)

    with pytest.raises(SkillUploadPackageUnavailableError):
        obs.fetch_package_sync(OBJECT_KEY, _digest(data))

    assert client.get_calls == 0


def test_fetch_maps_missing_object_to_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(stat_error=_s3_error("NoSuchKey"))
    _use_client(monkeypatch, client)

    with pytest.raises(SkillUploadExpiredError):
        obs.fetch_package_sync(OBJECT_KEY, _digest(b"zip-payload"))


def test_fetch_rejects_content_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = b"expected"
    client = _FakeClient(
        data=b"tampered", metadata={"x-amz-meta-sha256": _digest(expected)}
    )
    _use_client(monkeypatch, client)

    with pytest.raises(SkillUploadPackageUnavailableError):
        obs.fetch_package_sync(OBJECT_KEY, _digest(expected))
