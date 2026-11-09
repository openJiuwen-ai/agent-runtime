# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""manager 只读预览：内存解包、路径安全、文件类型与过期语义。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from manager_server.core.approval.service import ApprovalService
from manager_server.core.skill_upload.errors import (
    SkillUploadExpiredError,
    SkillUploadNotFoundError,
    SkillUploadPackageInvalidError,
)
from manager_server.core.skill_upload.preview import build_preview, read_file
from manager_server.core.skill_upload.service import SkillUploadService

pytestmark = pytest.mark.unit

OBJECT_KEY = f"skill-approvals/{'0' * 64}/{'a' * 32}/demo.zip"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _demo_package() -> bytes:
    return _zip(
        {
            "demo/SKILL.md": b"# Demo Skill\n\nHello.\n",
            "demo/scripts/run.py": b"print('hi')\n",
            "demo/assets/logo.png": b"\x89PNG\r\n\x1a\n",
            "demo/data.bin": b"\x00\x01\x02",
            "README.md": b"outside skill root",
        }
    )


def _order(*, content: bytes, expires_in_days: int = 1) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    return {
        "business_type": "skill_upload",
        "expires_at": expires,
        "apply_data": {
            "skill_name": "demo-skill",
            "skill_version": "1.2.3",
            "description": "demo description",
            "package": {
                "filename": "demo.zip",
                "object_key": OBJECT_KEY,
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
            },
        },
    }


def test_build_preview_reads_skill_md_and_relative_files() -> None:
    preview = build_preview(_demo_package())

    assert preview["skill_md"].startswith("# Demo Skill")
    assert preview["skill_md_truncated"] is False
    files = {item["path"]: item for item in preview["files"]}
    assert set(files) == {
        "SKILL.md",
        "scripts/run.py",
        "assets/logo.png",
        "data.bin",
    }
    assert files["scripts/run.py"]["kind"] == "text"
    assert files["assets/logo.png"]["kind"] == "image"
    assert files["data.bin"]["kind"] == "binary"


def test_build_preview_rejects_zip_slip() -> None:
    package = _zip({"../evil.txt": b"x", "SKILL.md": b"# demo"})

    with pytest.raises(SkillUploadPackageInvalidError):
        build_preview(package)


def test_build_preview_rejects_missing_skill_md() -> None:
    package = _zip({"README.md": b"# no skill"})

    with pytest.raises(SkillUploadPackageInvalidError, match="SKILL.md"):
        build_preview(package)


def test_build_preview_rejects_invalid_archive() -> None:
    with pytest.raises(SkillUploadPackageInvalidError):
        build_preview(b"not-a-zip")


def test_read_file_returns_media_type_and_text() -> None:
    package = _demo_package()

    content, media_type, truncated = read_file(package, "scripts/run.py")
    assert content == b"print('hi')\n"
    assert media_type.startswith("text/")
    assert truncated is False

    image, image_type, _ = read_file(package, "assets/logo.png")
    assert image_type == "image/png"
    assert image.startswith(b"\x89PNG")


def test_read_file_rejects_path_outside_skill_root() -> None:
    with pytest.raises(SkillUploadPackageInvalidError):
        read_file(_demo_package(), "../README.md")

    with pytest.raises(SkillUploadNotFoundError):
        read_file(_demo_package(), "README.md")


@pytest.mark.asyncio
async def test_read_preview_returns_metadata(manager_api) -> None:
    package = _demo_package()
    order = _order(content=package)
    fetch = AsyncMock(return_value=package)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=order)
    ), patch("manager_server.core.skill_upload.service.fetch_package", fetch):
        preview = await SkillUploadService(manager_api.handler).read_preview(
            order_num="apr_x"
        )

    assert preview["order_num"] == "apr_x"
    assert preview["skill_name"] == "demo-skill"
    assert preview["skill_version"] == "1.2.3"
    assert preview["description"] == "demo description"
    assert preview["expires_at"]
    assert preview["skill_md"].startswith("# Demo Skill")
    fetch.assert_awaited_once_with(OBJECT_KEY, order["apply_data"]["package"]["sha256"])


@pytest.mark.asyncio
async def test_read_preview_rejects_expired_order(manager_api) -> None:
    package = _demo_package()
    order = _order(content=package, expires_in_days=-1)
    fetch = AsyncMock(return_value=package)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=order)
    ), patch(
        "manager_server.core.skill_upload.service.fetch_package", fetch
    ), pytest.raises(SkillUploadExpiredError):
        await SkillUploadService(manager_api.handler).read_preview(
            order_num="apr_x"
        )

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_preview_file_rejects_path_traversal(manager_api) -> None:
    package = _demo_package()
    order = _order(content=package)
    fetch = AsyncMock(return_value=package)

    with patch.object(
        ApprovalService, "get_order", AsyncMock(return_value=order)
    ), patch(
        "manager_server.core.skill_upload.service.fetch_package", fetch
    ), pytest.raises(SkillUploadPackageInvalidError):
        await SkillUploadService(manager_api.handler).read_preview_file(
            order_num="apr_x", path="../SKILL.md"
        )
