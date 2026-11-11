# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""manager 直读 OBS：endpoint 归一化与华为 OBS 虚拟主机寻址。"""

from __future__ import annotations

import minio
import pytest

from manager_server.core.skill_upload import obs
from manager_server.core.skill_upload.errors import SkillUploadUnavailableError

pytestmark = pytest.mark.unit


class _StubMinio:
    """记录 endpoint 与是否切换虚拟主机寻址。"""

    instances: list["_StubMinio"] = []

    def __init__(self, endpoint: str, /, **kwargs: object) -> None:
        self.endpoint = endpoint
        self.kwargs = kwargs
        self.virtual = False
        _StubMinio.instances.append(self)

    def enable_virtual_style_endpoint(self) -> None:
        self.virtual = True


@pytest.fixture
def obs_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(obs.settings, "obs_url", "obs.cn-north-4.myhuaweicloud.com")
    monkeypatch.setattr(obs.settings, "obs_bucket", "artifacts")
    monkeypatch.setattr(obs.settings, "obs_access_key", "test-access")
    monkeypatch.setattr(obs.settings, "obs_secret_key", "test-secret")
    monkeypatch.setattr(obs.settings, "obs_region", "cn-north-4")
    monkeypatch.setattr(obs.settings, "obs_secure", "true")
    monkeypatch.setattr(minio, "Minio", _StubMinio)
    _StubMinio.instances = []


@pytest.mark.parametrize(
    ("endpoint", "bucket", "expected_endpoint", "virtual"),
    [
        (
            "obs.cn-north-4.myhuaweicloud.com",
            "artifacts",
            "obs.cn-north-4.myhuaweicloud.com",
            True,
        ),
        (
            "https://ARTIFACTS.obs.cn-north-4.myhuaweicloud.com.:443/",
            "artifacts",
            "obs.cn-north-4.myhuaweicloud.com",
            True,
        ),
        (
            "obs.cn-north-4.myhuaweicloud.com:9443",
            "obs",
            "obs.cn-north-4.myhuaweicloud.com:9443",
            True,
        ),
        (
            "http://minio-headless.default:9000",
            "artifacts",
            "minio-headless.default:9000",
            False,
        ),
        ("https://s3.example.com", "artifacts", "s3.example.com", False),
    ],
)
def test_store_selects_addressing_style(
    obs_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    bucket: str,
    expected_endpoint: str,
    virtual: bool,
) -> None:
    monkeypatch.setattr(obs.settings, "obs_url", endpoint)
    monkeypatch.setattr(obs.settings, "obs_bucket", bucket)

    client, returned_bucket = obs._store()  # pylint: disable=protected-access

    assert client.endpoint == expected_endpoint
    assert client.virtual is virtual
    assert returned_bucket == bucket


def test_store_keeps_secure_and_region_for_obs(
    obs_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIUWENSWARM_SSL_VERIFY", "false")

    client, _ = obs._store()  # pylint: disable=protected-access

    assert client.kwargs["secure"] is True
    assert client.kwargs["region"] == "cn-north-4"
    assert client.kwargs["access_key"] == "test-access"
    assert client.kwargs["secret_key"] == "test-secret"


def test_store_requires_configuration(
    obs_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(obs.settings, "obs_url", "")

    with pytest.raises(SkillUploadUnavailableError):
        obs._store()  # pylint: disable=protected-access

    assert _StubMinio.instances == []


def test_store_rejects_invalid_port(
    obs_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        obs.settings, "obs_url", "obs.cn-north-4.myhuaweicloud.com:notaport"
    )

    with pytest.raises(SkillUploadUnavailableError):
        obs._store()  # pylint: disable=protected-access


def test_store_rejects_user_information(
    obs_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        obs.settings, "obs_url", "https://user@obs.cn-north-4.myhuaweicloud.com"
    )

    with pytest.raises(SkillUploadUnavailableError):
        obs._store()  # pylint: disable=protected-access

    assert _StubMinio.instances == []
