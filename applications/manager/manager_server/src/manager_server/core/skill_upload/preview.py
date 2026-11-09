"""企业版 Skill 按包上传审批：管理面只读预览。

原始 ZIP 只在内存中解包，不落盘；浏览器只能拿到文件清单与单个文件内容。
安全限制与上传/安装侧保持一致（文件数量、单文件大小、解包总量、目录深度、
Zip Slip 与符号链接）。
"""

from __future__ import annotations

import io
import mimetypes
import stat
import zipfile
import zlib
from typing import Any

from manager_server.core.skill_upload.errors import (
    SkillUploadNotFoundError,
    SkillUploadPackageInvalidError,
)

_MAX_FILES = 5000
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 100 * 1024 * 1024
_MAX_DEPTH = 20
_MAX_TEXT_BYTES = 1024 * 1024

_SKILL_MD = "skill.md"

_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "ico": "image/x-icon",
    "avif": "image/avif",
}

_TEXT_MEDIA_TYPES = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "mdx": "text/markdown",
    "json": "application/json",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "csv": "text/csv",
}

_TEXT_EXTENSIONS = {
    *_TEXT_MEDIA_TYPES,
    "txt",
    "log",
    "rst",
    "py",
    "pyw",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "vue",
    "toml",
    "ini",
    "cfg",
    "conf",
    "env",
    "sh",
    "bash",
    "zsh",
    "ps1",
    "bat",
    "cmd",
    "html",
    "htm",
    "css",
    "scss",
    "less",
    "xml",
    "tsv",
    "sql",
    "java",
    "c",
    "h",
    "cc",
    "cpp",
    "hpp",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "swift",
    "kt",
    "gradle",
    "properties",
}

_SPECIAL_TEXT_NAMES = {
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".editorconfig",
    ".npmrc",
    ".prettierrc",
    ".eslintrc",
    ".babelrc",
}

_ZIP_ERRORS = (
    zipfile.BadZipFile,
    OSError,
    RuntimeError,
    NotImplementedError,
    zlib.error,
)


def _invalid(message: str) -> SkillUploadPackageInvalidError:
    return SkillUploadPackageInvalidError(message)


def _safe_parts(name: str) -> tuple[str, ...] | None:
    """规范化 ZIP 内路径；非法路径直接拒绝，空路径返回 ``None``。"""
    raw = (name or "").replace("\\", "/")
    if not raw:
        return None
    if "\0" in raw or raw.startswith("/"):
        raise _invalid("package contains an invalid path")
    parts = tuple(part for part in raw.split("/") if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise _invalid("package contains an invalid path")
    if len(parts) > _MAX_DEPTH:
        raise _invalid("package directory depth exceeds the limit")
    return parts or None


def _open_zip(package: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(package))
    except _ZIP_ERRORS as exc:
        raise _invalid("package is not a valid zip archive") from exc


def _index_files(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    files: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        parts = _safe_parts(info.filename)
        if parts is None:
            continue
        if stat.S_ISLNK(int(info.external_attr or 0) >> 16):
            raise _invalid("package contains a symbolic link")
        if int(info.flag_bits or 0) & 0x1:
            raise _invalid("package contains an encrypted file")
        size = int(info.file_size or 0)
        if size > _MAX_FILE_BYTES:
            raise _invalid("package contains a file larger than the allowed limit")
        total += size
        if total > _MAX_TOTAL_BYTES:
            raise _invalid("package is larger than the allowed limit")
        if len(files) >= _MAX_FILES:
            raise _invalid("package contains too many files")
        path = "/".join(parts)
        if path in files:
            raise _invalid("package contains duplicate paths")
        files[path] = info
    return files


def _skill_root(files: dict[str, zipfile.ZipInfo]) -> str:
    candidates = [
        path for path in files if path.rsplit("/", 1)[-1].lower() == _SKILL_MD
    ]
    if not candidates:
        raise _invalid("package does not contain SKILL.md")
    target = min(candidates, key=lambda path: (path.count("/"), path))
    head, _, _ = target.rpartition("/")
    return head


def _relative_files(
    files: dict[str, zipfile.ZipInfo], root: str
) -> dict[str, zipfile.ZipInfo]:
    prefix = f"{root}/" if root else ""
    relative: dict[str, zipfile.ZipInfo] = {}
    for path, info in files.items():
        if prefix and not path.startswith(prefix):
            continue
        relative[path[len(prefix):]] = info
    return relative


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    _, dot, ext = name.rpartition(".")
    return ext if dot else ""


def _kind(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name in _SPECIAL_TEXT_NAMES:
        return "text"
    ext = _extension(path)
    if ext in _IMAGE_MEDIA_TYPES:
        return "image"
    if ext in _TEXT_EXTENSIONS:
        return "text"
    return "binary"


def _media_type(path: str) -> str:
    ext = _extension(path)
    media_type = _IMAGE_MEDIA_TYPES.get(ext) or _TEXT_MEDIA_TYPES.get(ext)
    if media_type:
        return media_type
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    try:
        with zf.open(info) as handle:
            return handle.read(limit)
    except _ZIP_ERRORS as exc:
        raise _invalid("package content cannot be read") from exc


def build_preview(package: bytes) -> dict[str, Any]:
    """解析原始包，返回 SKILL.md 正文与相对技能根目录的文件清单。"""
    with _open_zip(package) as zf:
        files = _index_files(zf)
        root = _skill_root(files)
        relative = _relative_files(files, root)
        skill_md_path = min(
            (
                path
                for path in relative
                if path.rsplit("/", 1)[-1].lower() == _SKILL_MD
            ),
            key=lambda path: (path.count("/"), path),
        )
        raw = _read_member(zf, relative[skill_md_path], _MAX_TEXT_BYTES + 1)
        truncated = len(raw) > _MAX_TEXT_BYTES

        return {
            "skill_md": raw[:_MAX_TEXT_BYTES].decode("utf-8-sig", errors="replace"),
            "skill_md_truncated": truncated,
            "files": [
                {
                    "path": path,
                    "size": int(info.file_size or 0),
                    "kind": _kind(path),
                }
                for path, info in sorted(relative.items())
            ],
        }


def read_file(package: bytes, path: str) -> tuple[bytes, str, bool]:
    """读取技能根目录下的单个文件；文本最多返回 1 MiB。"""
    parts = _safe_parts(path)
    if parts is None:
        raise SkillUploadNotFoundError("file not found in the package")
    requested = "/".join(parts)

    with _open_zip(package) as zf:
        files = _index_files(zf)
        relative = _relative_files(files, _skill_root(files))
        info = relative.get(requested)
        if info is None:
            raise SkillUploadNotFoundError("file not found in the package")

        is_text = _kind(requested) == "text"
        raw = _read_member(
            zf, info, _MAX_TEXT_BYTES + 1 if is_text else _MAX_FILE_BYTES
        )
        truncated = is_text and len(raw) > _MAX_TEXT_BYTES
        return (
            raw[:_MAX_TEXT_BYTES] if truncated else raw,
            _media_type(requested),
            truncated,
        )
