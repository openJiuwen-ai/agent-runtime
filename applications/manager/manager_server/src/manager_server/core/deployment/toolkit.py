"""部署工具树定位与可用性检查（deploy/openshift 随镜像分发）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from manager_server.infrastructure.config import settings

# 镜像内工具树位置（docker/Dockerfile.base COPY 整仓）
_IMAGE_TOOL_DIR = Path("/app/agent-runtime/deploy/openshift")


def resolve_tool_dir() -> Path:
    """工具树目录：配置显式指定 > 镜像内路径 > 源码仓相对路径。"""
    configured = (settings.deploy_tool_dir or "").strip()
    if configured:
        return Path(configured)
    if _IMAGE_TOOL_DIR.is_dir():
        return _IMAGE_TOOL_DIR
    # 源码运行：manager_server/src/manager_server/core/deployment/ → 仓根
    return Path(__file__).resolve().parents[7] / "deploy" / "openshift"


def read_tool_version(tool_dir: Path | None = None) -> str:
    """读工具树版本戳（deploy/openshift/TOOL_VERSION）；缺失返回 unknown。"""
    path = (tool_dir or resolve_tool_dir()) / "TOOL_VERSION"
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return version or "unknown"


def check_tool_available(tool_dir: Path | None = None) -> tuple[bool, str]:
    """部署前置检查：deploy.sh 存在、kubectl/yq 可用（deploy.sh up 实际执行依赖）。

    返回 ``(available, reason)``；不可用时 reason 说明缺什么（API 据此返回 503）。
    """
    if not settings.deploy_enabled:
        return False, "部署功能未启用（MANAGER_DEPLOY_ENABLED）"
    directory = tool_dir or resolve_tool_dir()
    if not (directory / "deploy.sh").is_file():
        return False, f"部署工具树不可用: {directory}"
    missing = [cmd for cmd in ("kubectl", "yq") if shutil.which(cmd) is None]
    if missing:
        return False, f"缺少依赖命令: {', '.join(missing)}"
    kubeconfig = (settings.deploy_kubeconfig or "").strip()
    if kubeconfig and not Path(kubeconfig).is_file():
        return False, f"kubeconfig 不存在: {kubeconfig}"
    return True, ""


def parse_env_example(tool_dir: Path | None = None) -> dict:
    """解析工具树 .env.example 为键值 dict（默认参数播种用；兼容带/不带引号）。

    行内注释（``KEY=value  # 注释``）一并剥离——example 的值均为占位演示，
    不存在合法含 ``#`` 的默认值。
    """
    path = (tool_dir or resolve_tool_dir()) / ".env.example"
    result: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return result
    for line in content.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.split("#", 1)[0].strip().strip('"').strip("'")
        if key.isidentifier():
            result[key] = value
    return result


# global_vars.sh declare -A DEPLOY_VARS 的默认值行：["KEY"]="VALUE"
_TOOL_DEFAULT_RE = re.compile(r'^\s*\["([A-Z0-9_]+)"\]="([^"]*)"\s*$', re.M)


def parse_tool_defaults(tool_dir: Path | None = None) -> dict:
    """解析工具树 global_vars.sh 的 DEPLOY_VARS 内置默认值（镜像清单等真源）。"""
    path = (tool_dir or resolve_tool_dir()) / "global_vars.sh"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return dict(_TOOL_DEFAULT_RE.findall(content))
