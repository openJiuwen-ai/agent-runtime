# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""虚拟环境管理器"""

import shutil
import subprocess
import sys
import os
import shlex
import time
from pathlib import Path
from typing import Optional
from .log import get_logger

from .config import settings

logger = get_logger(__name__)


def _resolve_uv_executable() -> str:
    """将 uv 解析为绝对路径，满足外部进程调用规范。"""
    resolved = shutil.which("uv")
    if not resolved:
        raise RuntimeError("uv executable not found in PATH")
    return resolved


class VirtualEnvironmentManager:
    """
    虚拟环境管理器

    负责为每个部署创建、管理和清理独立的虚拟环境。
    """
    @staticmethod
    def get_venv_path(deployment_id: str) -> Path:
        """
        根据部署ID获取虚拟环境路径

        Args:
            deployment_id: 部署ID

        Returns:
            虚拟环境根目录路径
        """
        return settings.deploy_path / deployment_id / ".venv"


    def create_venv(self, deployment_id: str) -> Path:
        """
        为部署创建独立的虚拟环境

        Args:
            deployment_id: 部署ID，用作虚拟环境目录名

        Returns:
            虚拟环境路径

        Raises:
            RuntimeError: 虚拟环境创建失败
        """
        venv_path = self.get_venv_path(deployment_id)

        if venv_path.exists():
            logger.info("Virtual environment already exists: %s", venv_path)
            return venv_path

        logger.info("Creating virtual environment: %s", venv_path)

        try:
            # 使用 uv 创建虚拟环境
            uv_exe = _resolve_uv_executable()
            result = subprocess.run(
                [uv_exe, "venv", str(venv_path)],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                logger.warning(
                    "venv command returned code %s: %s",
                    result.returncode,
                    result.stderr,
                )

            if not venv_path.exists():
                logger.error("Virtual environment directory not created: %s", venv_path)
                raise RuntimeError(f"Failed to create venv: directory not created")

            python_exe = self.get_python_executable(deployment_id)

            if sys.platform == "win32":
                pip_exe = venv_path / "Scripts" / "pip.exe"
            else:
                pip_exe = venv_path / "bin" / "pip"

            if not python_exe.exists():
                logger.error("Python executable not found in venv: %s", python_exe)
                raise RuntimeError(f"Failed to create venv: python executable not found")

            if not pip_exe.exists():
                logger.info("pip not found, running ensurepip...")
                ensure_result = subprocess.run(
                    [str(python_exe), "-m", "ensurepip", "--upgrade"],
                    capture_output=True,
                    text=True
                )
                if ensure_result.returncode != 0:
                    logger.warning(
                        "ensurepip returned code %s: %s",
                        ensure_result.returncode,
                        ensure_result.stderr,
                    )
                    get_pip_result = subprocess.run(
                        [str(python_exe), "-m", "pip", "install", "--upgrade", "pip"],
                        capture_output=True,
                        text=True
                    )
                    if get_pip_result.returncode != 0:
                        logger.error("Failed to bootstrap pip: %s", get_pip_result.stderr)
                        raise RuntimeError(f"Failed to create venv: pip not available")

            logger.info("Virtual environment created successfully: %s", venv_path)
            return venv_path
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create virtual environment: %s", e.stderr)
            raise RuntimeError(f"Failed to create venv: {e}") from e

    def get_python_executable(self, deployment_id: str) -> Path:
        """
        获取虚拟环境中的Python可执行文件路径

        Args:
            deployment_id: 部署ID

        Returns:
            Python可执行文件路径

        Raises:
            RuntimeError: Python可执行文件未找到
        """
        venv_path = self.get_venv_path(deployment_id)

        if not venv_path.exists():
            raise RuntimeError(f"Virtual environment not found: {venv_path}")

        if sys.platform == "win32":
            # Windows: Scripts/python.exe
            python_path = venv_path / "Scripts" / "python.exe"
        else:
            # Linux/Mac: bin/python
            python_path = venv_path / "bin" / "python"

        if not python_path.exists():
            raise RuntimeError(f"Python executable not found: {python_path}")

        return python_path

    def pip_install(self, deployment_id: str, package: str) -> bool:
        """
        Install a specified PyPI package in the isolated virtual environment.
        Auto retry on network timeout error, with uv cache resume support.

        Args:
            deployment_id: Unique identifier for the deployment
            package: Package name with optional version specifier,
                    e.g. requests, fastapi==0.115.11, local wheel path

        Returns:
            True if installation succeeded, False otherwise
        """
        python_executable = self.get_python_executable(deployment_id)
        uv_extra_args_list = shlex.split(settings.UV_EXTRA_ARGS.strip())

        logger.info("Installing package: %s into venv [%s]", package, deployment_id)

        uv_exe = _resolve_uv_executable()
        cmd = [
            uv_exe, "pip", "install",
            package,
            "--python", str(python_executable),
            *uv_extra_args_list
        ]
        logger.debug("Execute install command: %s", " ".join(cmd))

        max_retry = 3
        retry_delay = 5
        retry_count = 0

        timeout_keywords = {
            "operation timed out",
            "network timeout",
            "request or response body error",
            "failed to fetch",
            "timeout"
        }

        while retry_count < max_retry:
            try:
                env = os.environ.copy()
                env["VIRTUAL_ENV"] = str(self.get_venv_path(deployment_id))
                # Extended timeout for slow private pypi
                env["UV_HTTP_TIMEOUT"] = "600"
                env["UV_REQUEST_TIMEOUT"] = "600"
                # Disable concurrent download for unstable internal repo
                env["UV_CONCURRENT_DOWNLOADS"] = "1"

                result = subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )

                if result.returncode == 0:
                    logger.info("Package installed successfully: %s", package)
                    return True

                # Check whether error is network timeout related
                stderr_lower = result.stderr.lower()
                is_timeout_error = any(kw in stderr_lower for kw in timeout_keywords)

                logger.error(
                    "Package install failed | exit_code=%s | retry=%s/%s | stderr=%s",
                    result.returncode, retry_count + 1, max_retry, result.stderr
                )

                if not is_timeout_error:
                    # Non-timeout error, no retry
                    return False

                retry_count += 1
                if retry_count < max_retry:
                    logger.warning(
                        "Network timeout detected, retry after %s seconds",
                        retry_delay
                    )
                    time.sleep(retry_delay)

            except Exception as e:
                logger.error(
                    "Exception occurred while installing package %s: %s",
                    package, str(e)
                )
                retry_count += 1
                if retry_count < max_retry:
                    time.sleep(retry_delay)

        logger.error("Reached maximum retry limit, install failed: %s", package)
        return False


    def delete_venv(self, deployment_id: str) -> bool:
        """
        删除部署的虚拟环境

        Args:
            deployment_id: 部署ID

        Returns:
            是否删除成功
        """
        venv_path = self.get_venv_path(deployment_id)

        if not venv_path.exists():
            logger.warning("Virtual environment not found: %s", venv_path)
            return False

        logger.info("Deleting virtual environment: %s", venv_path)

        try:
            shutil.rmtree(venv_path)
            logger.info("Virtual environment deleted: %s", venv_path)
            return True
        except Exception as e:
            logger.error("Failed to delete virtual environment: %s", e)
            return False
