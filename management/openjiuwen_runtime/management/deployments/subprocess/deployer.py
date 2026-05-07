# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""本地进程部署器

特性:
- 为每个部署创建独立的虚拟环境
- 支持WHL包部署
- 使用 python -m package_name 方式运行
- 支持跨进程管理（通过PID）
"""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

from openjiuwen_runtime.foundation.config import settings
from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.foundation.log.utils import mask_userdata
from openjiuwen_runtime.foundation.venv_manager import VirtualEnvironmentManager

from ...models.enums import DeploymentStatus
from ..base.deployer import Deployer
from ..base.models import DeployContext, DeployResult
from .models import SubprocessParams

logger = get_logger(__name__)


def _windows_system32_exe(filename: str) -> str:
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(system_root) / "System32" / filename)


class LocalSubprocessDeployer(Deployer[SubprocessParams]):
    """本地进程部署器"""

    def __init__(
            self,
            default_host: str = "127.0.0.1",
            default_port_start: int = 8000,
    ):
        self.default_host = default_host
        self.default_port_start = default_port_start
        self.venv_manager = VirtualEnvironmentManager()
        self._processes: Dict[str, asyncio.subprocess.Process] = {}

    def _kill_by_pid(self, pid: int) -> bool:
        """通过 PID 终止进程（跨进程有效）"""
        try:
            if sys.platform == "win32":
                taskkill = _windows_system32_exe("taskkill.exe")
                result = subprocess.run(
                    [taskkill, "/F", "/PID", str(pid)],
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                success = result.returncode == 0
                if success:
                    logger.info("Killed process %s", pid)
                else:
                    logger.warning("Failed to kill process %s: %s", pid, result.stderr)
                return success
            else:
                result = subprocess.run(
                    ["/bin/kill", "-9", str(pid)],
                    capture_output=True,
                    text=True,
                )
                success = result.returncode == 0
                if success:
                    logger.info("Killed process %s", pid)
                else:
                    logger.warning("Failed to kill process %s", pid)
                return success
        except Exception as e:
            logger.error("Error killing process %s: %s", pid, e)
            return False

    def _check_process_by_pid(self, pid: int) -> bool:
        """通过 PID 检查进程是否运行"""
        if sys.platform == "win32":
            tasklist = _windows_system32_exe("tasklist.exe")
            result = subprocess.run(
                [tasklist, "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
            )
            return str(pid) in result.stdout
        else:
            result = subprocess.run(
                ["/bin/kill", "-0", str(pid)],
                capture_output=True,
            )
            return result.returncode == 0

    def _get_venv_python_path(self, venv_path: str) -> str:
        """获取虚拟环境中的 Python 可执行文件路径"""
        if os.name == "nt":
            return os.path.join(venv_path, "Scripts", "python.exe")
        return os.path.join(venv_path, "bin", "python")

    def _get_venv_pip_path(self, venv_path: str) -> str:
        """获取虚拟环境中的 pip 可执行文件路径"""
        if os.name == "nt":
            return os.path.join(venv_path, "Scripts", "pip.exe")
        return os.path.join(venv_path, "bin", "pip")

    async def deploy(self, ctx: DeployContext[SubprocessParams]) -> DeployResult:
        """使用WHL包部署应用

        流程:
        1. 创建独立虚拟环境
        2. 在虚拟环境中安装WHL包
        3. 使用 python -m name 启动应用

        Args:
            ctx: 部署上下文参数

        Returns:
            DeployResult: 部署结果
        """
        deployment_id = ctx.deployment_id
        logger.info("Deploying subprocess: deployment_id=%s, host=%s", ctx.deployment_id, ctx.host)
        try:
            subprocess_params = ctx.params or SubprocessParams()
            whl_path = subprocess_params.whl_path
            ir_path = subprocess_params.ir_path
            userdata = subprocess_params.userdata
            if ir_path:
                package_name = 'openjiuwen_runtime.examples.lowcode_agent'
            else:
                package_name = subprocess_params.package_name

            # 创建虚拟环境
            venv_path = self.venv_manager.create_venv(deployment_id)
            logger.info("Virtual environment created: %s", venv_path)

            # 获取所有 .whl 文件
            logger.info("dist_path: %s", settings.dist_path)
            whl_files = list(settings.dist_path.glob("*.whl"))
            if not whl_files:
                raise RuntimeError(f"No .whl files found in dist directory: {settings.dist_path}")

            logger.info("whl_files: %s", whl_files)

            # 循环安装所有 whl 包
            for whl_file in whl_files:
                self.venv_manager.install_whl(deployment_id, str(whl_file))
                logger.info("Installed WHL package: %s", whl_file)

            # 获取虚拟环境Python解释器
            python_executable = self.venv_manager.get_python_executable(deployment_id)

            # 5. 构建启动命令: python -m name --host --port [--irpath ir_path] [--userdata userdata]
            if not package_name:
                raise RuntimeError("package_name is required for subprocess deployment")

            cmd = [
                str(python_executable),
                "-m",
                package_name,
                "--host", "0.0.0.0",
                "--port", str(ctx.port)
            ]
            if ir_path:
                cmd.extend(["--irpath", ir_path])
            logger.debug("Command: %s", cmd)

            # 6. 启动进程 (Windows: 使用新进程组，避免Ctrl+C影响子进程)
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            env = os.environ.copy()
            env["VIRTUAL_ENV"] = str(venv_path)
            # 通过环境变量传递 userdata，避免在 ps/任务管理器中泄露敏感信息
            if userdata:
                env["RUNTIME_USERDATA"] = userdata
                logger.info("Using userdata: %s", mask_userdata(userdata))

            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )

            # 6. 等待进程启动并检查状态
            await asyncio.sleep(2)

            if process.poll() is not None:
                # 进程已经退出，读取错误信息
                stdout, stderr = process.communicate()
                stderr_txt = stderr.decode("utf-8", errors="ignore")
                stdout_txt = stdout.decode("utf-8", errors="ignore")
                error_msg = stderr_txt or stdout_txt or "Unknown error"
                logger.error("Process exited for %s: %s", deployment_id, error_msg)
                raise RuntimeError(f"Process exited: {error_msg}")

            url = f"http://{settings.IP}:{ctx.port}/"
            logger.info(
                "Deployment %s succeeded, PID: %s, URL: %s",
                deployment_id,
                process.pid,
                url,
            )

            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message="Deployment started successfully",
                url=url,
                pid=process.pid,
                venv_path=str(venv_path),
            )

        except Exception as e:
            logger.error(
                "Subprocess deploy failed: deployment_id=%s, error=%s",
                ctx.deployment_id,
                str(e),
            )
            return DeployResult(success=False,
                                deployment_id=deployment_id, message=f"Deployment failed: {str(e)}")

    async def stop(self, deployment_id: str, **kwargs) -> DeployResult:
        """停止部署并清理虚拟环境

        Args:
            deployment_id: 部署ID
            **kwargs: pid (进程PID), venv_path (虚拟环境路径)

        Returns:
            DeployResult: 停止结果
        """
        pid = kwargs.get("pid")
        venv_path = kwargs.get("venv_path")
        logger.info("Stopping subprocess: deployment_id=%s, pid=%s", deployment_id, pid)
        try:
            if deployment_id in self._processes:
                process = self._processes[deployment_id]
                logger.debug(
                    "Terminating process: deployment_id=%s, pid=%s",
                    deployment_id,
                    process.pid,
                )
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Process did not terminate gracefully, killing: deployment_id=%s",
                        deployment_id,
                    )
                    process.kill()
                del self._processes[deployment_id]
            elif pid:
                success = self._kill_by_pid(pid)
                if not success:
                    logger.warning("Failed to kill process by pid: %s", pid)

            if venv_path and os.path.exists(venv_path):
                logger.debug("Deleting virtual environment: %s", venv_path)
                shutil.rmtree(venv_path, ignore_errors=True)

            logger.info("Subprocess stopped: deployment_id=%s", deployment_id)
            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message="Deployment stopped successfully"
            )

        except Exception as e:
            logger.error(
                "Subprocess stop failed: deployment_id=%s, error=%s",
                deployment_id,
                str(e),
            )
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Stop failed: {str(e)}"
            )

    async def get_status(self, deployment_id: str, **kwargs) -> DeploymentStatus:
        """获取部署状态

        Args:
            deployment_id: 部署ID
            **kwargs: pid (进程PID)

        Returns:
            DeploymentStatus: 部署状态
        """
        pid = kwargs.get("pid")
        logger.debug(
            "Getting subprocess status: deployment_id=%s, pid=%s",
            deployment_id,
            pid,
        )

        if deployment_id in self._processes:
            process = self._processes[deployment_id]
            if process.returncode is None:
                return DeploymentStatus.RUNNING
            else:
                return DeploymentStatus.STOPPED

        if pid:
            is_running = self._check_process_by_pid(pid)
            return DeploymentStatus.RUNNING if is_running else DeploymentStatus.STOPPED

        return DeploymentStatus.PENDING

    def _get_available_port(self) -> int:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port
