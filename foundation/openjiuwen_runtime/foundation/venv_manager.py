# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""虚拟环境管理器"""

import shutil
import subprocess
import sys
import os
import shlex
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


    def install_whl(self, deployment_id: str, whl_path: str) -> bool:
        """
        在虚拟环境中安装WHL包

        Args:
            deployment_id: 部署ID
            whl_path: WHL包文件路径

        Returns:
            是否安装成功

        Raises:
            RuntimeError: 安装失败
        """
        python_executable = self.get_python_executable(deployment_id)

        logger.info("Installing WHL package: %s into %s", whl_path, deployment_id)

        uv_extra_args_list = shlex.split(settings.UV_EXTRA_ARGS.strip())

        # 使用 uv pip 安装包，通过虚拟环境路径指定目标环境
        uv_exe = _resolve_uv_executable()
        cmd = [
            uv_exe, "pip", "install",
            whl_path,
            "--python", str(python_executable),
            *uv_extra_args_list
        ]
        logger.debug("Command: %s", " ".join(cmd))

        try:
            env = os.environ.copy()
            env["VIRTUAL_ENV"] = str(self.get_venv_path(deployment_id))
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
            )

            if result.returncode != 0:
                logger.warning(
                    "pip install returned code %s: %s",
                    result.returncode,
                    result.stderr,
                )

            result = subprocess.run(
                [str(python_executable), "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                encoding='utf-8',
            )

            if result.returncode == 0:
                import json
                installed_packages = json.loads(result.stdout)
                whl_name = Path(whl_path).stem.split("-")[0].lower().replace("_", "-")
                for pkg in installed_packages:
                    if pkg["name"].lower().replace("_", "-") == whl_name:
                        logger.info("WHL package installed successfully: %s", whl_path)
                        return True

                logger.error("WHL package not found in installed list: %s", whl_path)
                raise RuntimeError(
                    "Failed to install WHL package: package not in pip list"
                ) from None
            else:
                logger.error("Failed to verify installation: %s", result.stderr)
                raise RuntimeError("Failed to verify WHL installation") from None

        except Exception as e:
            logger.error("Failed to install WHL: %s", e)
            raise RuntimeError(f"Failed to install WHL package: {e}") from e

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
