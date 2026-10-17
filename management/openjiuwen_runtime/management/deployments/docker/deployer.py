# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Docker 部署器

特性:
- 使用 Docker 容器部署应用
- 支持 WHL 包部署
- 支持环境变量和卷挂载
- 支持容器生命周期管理
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from openjiuwen_runtime.foundation.config import settings
from openjiuwen_runtime.foundation.log import get_logger

from ..base.deployer import Deployer
from ..base.models import DeployContext, DeployResult
from .models import DockerParams
from ...models.enums import DeploymentStatus
from .utils import find_available_port

logger = get_logger(__name__)


class DockerDeployer(Deployer[DockerParams]):
    """Docker 部署器"""

    def __init__(
            self,
            default_host: str = "localhost",
            docker_host: Optional[str] = None,
    ):
        self.default_host = default_host
        self.docker_host = docker_host
        self._containers: dict[str, str] = {}
        logger.debug("DockerDeployer initialized: docker_host=%s", docker_host)

    def _build_docker_command(self, *args: str) -> list[str]:
        """构建 Docker 命令"""
        cmd = ["docker"]
        if self.docker_host:
            cmd.extend(["-H", self.docker_host])
        cmd.extend(args)
        return cmd

    async def _run_docker_command(self, *args: str) -> tuple[bool, str]:
        """运行 Docker 命令"""
        cmd = self._build_docker_command(*args)

        logger.debug("Running docker command: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            return True, stdout.decode().strip()
        return False, stderr.decode().strip()


    async def deploy_lowcode_container(
        self,
        container_name: str,
        env_vars: dict,
        volumes: list,
        container_port: str,
        host_port: int,
        ir_source_path: str
    ) -> str:
        """
        创建低代码Agent容器：采用 docker create → docker cp → docker start 流程。

        规避问题：
            禁止使用 docker run -v 挂载容器内文件。
            因如果当前服务运行于容器中，需要挂载/var/run/docker.sock，
            所有挂载路径均指向宿主机，直接挂载容器内文件会导致目标文件被创建为空目录。
        """
        # 创建容器（不启动）
        create_args = ["create", "--name", container_name]

        # 环境变量
        if env_vars:
            for k, v in env_vars.items():
                create_args.extend(["-e", f"{k}={v}"])

        # 挂载卷
        if volumes:
            for vol in volumes:
                if "source" in vol and "target" in vol:
                    create_args.extend(["-v", f"{vol['source']}:{vol['target']}"])

        port_mapping = f"{host_port}:{container_port}"
        create_args.extend(["-p", port_mapping])

        # 镜像
        image_name = settings.LOWCODE_IMAGE
        if not image_name:
            raise RuntimeError("Environment variable LOWCODE_IMAGE is not set")
        create_args.append(image_name)

        logger.debug("Creating lowcode agent container: %s", container_name)
        success, output = await self._run_docker_command(*create_args)
        if not success:
            raise RuntimeError(f"create lowcode container failed: {output}")

        container_id = output.strip()
        logger.debug("container created: %s", container_id)

        # 复制 ir.json 到容器
        target_path = f"{container_name}:/app/ir.json"
        logger.debug("docker cp %s -> %s", ir_source_path, target_path)
        success, cp_out = await self._run_docker_command("cp", ir_source_path, target_path)
        if not success:
            raise RuntimeError(f"docker cp failed: {cp_out}")

        # 启动容器
        logger.debug("Start container: %s", container_name)
        success, start_out = await self._run_docker_command("start", container_id)
        if not success:
            raise RuntimeError(f"Start container failed: {start_out}")

        return container_id

    async def deploy(self, ctx: DeployContext[DockerParams]) -> DeployResult:
        """使用 Docker 容器部署应用
        Args:
            ctx: 部署上下文参数

        Returns:
            DeployResult: 部署结果
        """
        deployment_id = ctx.deployment_id
        logger.info("Deploying docker: deployment_id=%s, host=%s", deployment_id, ctx.host)

        try:
            docker_params = ctx.params or DockerParams()
            whl_path = docker_params.whl_path
            ir_path = docker_params.ir_path
            package_name = docker_params.package_name
            container_name = docker_params.container_name or f"deploy_{deployment_id}"
            env_vars = docker_params.env_vars or {}
            volumes = docker_params.volumes
            host = ctx.host or self.default_host or "127.0.0.1"
            iport = "8090"

            import json
            logger.debug(
                "docker_params 完整内容: \n%s",
                json.dumps(docker_params.__dict__, indent=2, ensure_ascii=False),
            )

            # Inject parent container proxy environment variables
            proxy_env = {
                "HTTP_PROXY": os.getenv("HTTP_PROXY", ""),
                "HTTPS_PROXY": os.getenv("HTTPS_PROXY", ""),
                "NO_PROXY": os.getenv("NO_PROXY", ""),
                "http_proxy": os.getenv("http_proxy", ""),
                "https_proxy": os.getenv("https_proxy", ""),
                "no_proxy": os.getenv("no_proxy", ""),
            }
            # Filter empty proxy values
            proxy_env_filtered = {k: v for k, v in proxy_env.items() if v}
            # Merge proxy env with custom env, custom env has higher priority
            env_vars = {**proxy_env_filtered, **env_vars}
            # link-auth：透传控制面的链路握手鉴权开关给 AgentServer 容器（容器不继承宿主
            # 环境）；显式 env 优先，故用 setdefault。enforce 下若缺，pod 不签 ack 令牌、
            # Gateway 反向校验失败。
            for _link_env in ("CLAW_LINK_AUTH_MODE", "CLAW_LINK_TOKEN_TTL"):
                _link_val = os.getenv(_link_env)
                if _link_val:
                    env_vars.setdefault(_link_env, _link_val)

            # 非低码情况
            if not ir_path:
                if not whl_path:
                    raise RuntimeError("whl_path is required for docker deployment")
                if not package_name:
                    raise RuntimeError("package_name is required for docker deployment")

            logger.debug("Start scanning host port range...")
            # settings.IP is the externally reachable host when configured; the
            # deploy context's host is the documented fallback (e.g. localhost /
            # a clean SDK install where the optional IP setting is empty).
            endpoint_host = settings.IP or host
            available_port = find_available_port(
                host=endpoint_host
            )

            if available_port:
                logger.info("Found available port: %d", available_port)
            else:
                raise RuntimeError(
                    f"No available port found on {endpoint_host!r}"
                )

            # 创建并启动低代码Agent容器
            container_id = await self.deploy_lowcode_container(
                container_name=container_name,
                env_vars=env_vars,
                volumes=volumes,
                container_port=iport,
                host_port=available_port,
                ir_source_path=ir_path
            )
            self._containers[deployment_id] = container_id

            url = f"http://{settings.IP or host}:{available_port}"
            logger.debug("url: %s", url)

            logger.info(
                "Docker deployed: deployment_id=%s, container=%s, url=%s",
                deployment_id,
                container_name,
                url,
            )
            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message="Docker deployment started successfully",
                url=url,
            )

        except Exception as e:
            logger.error("Docker deploy failed: deployment_id=%s, error=%s", deployment_id, str(e))
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Deployment failed: {str(e)}"
            )

    async def stop(self, deployment_id: str, **kwargs) -> DeployResult:
        """停止并删除 Docker 容器

        Args:
            deployment_id: 部署ID
            **kwargs: container_name (容器名称)

        Returns:
            DeployResult: 停止结果
        """
        container_name = kwargs.get("container_name") or f"deploy_{deployment_id}"
        logger.info(
            "Stopping docker: deployment_id=%s, container_name=%s",
            deployment_id,
            container_name,
        )

        try:
            # 停止容器
            logger.debug("Stopping container: container_name=%s", container_name)
            success, output = await self._run_docker_command("stop", container_name)
            if not success:
                logger.warning(
                    "Docker stop failed: deployment_id=%s, error=%s",
                    deployment_id,
                    output,
                )

            # 删除容器
            success, output = await self._run_docker_command("rm", container_name)
            if not success:
                logger.warning(
                    "Docker rm failed: deployment_id=%s, error=%s",
                    deployment_id,
                    output,
                )

            # 删除镜像
            # image_name = f"{deployment_id}:latest"
            # success, output = await self._run_docker_command("rmi", image_name)
            # if not success:
            #    logger.warning(f"Docker rmi failed: deployment_id={deployment_id}, error={output}")

            if deployment_id in self._containers:
                del self._containers[deployment_id]

            # 清理部署目录
            deploy_context_dir = settings.deploy_path / deployment_id
            logger.debug("Cleaning deploy directory: %s", deploy_context_dir)
            shutil.rmtree(deploy_context_dir, ignore_errors=True)

            logger.info("Docker stopped: deployment_id=%s", deployment_id)
            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message="Docker deployment stopped successfully"
            )

        except Exception as e:
            logger.error("Docker stop failed: deployment_id=%s, error=%s", deployment_id, str(e))
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Stop failed: {str(e)}"
            )

    async def get_status(self, deployment_id: str, **kwargs) -> DeploymentStatus:
        """获取 Docker 容器状态

        Args:
            deployment_id: 部署ID
            **kwargs: container_name (容器名称)

        Returns:
            DeploymentStatus: 部署状态
        """
        container_name = kwargs.get("container_name") or f"deploy_{deployment_id}"
        logger.debug(
            "Getting docker status: deployment_id=%s, container_name=%s",
            deployment_id,
            container_name,
        )

        success, output = await self._run_docker_command(
            "inspect", "-f", "{{.State.Status}}", container_name
        )

        if not success:
            return DeploymentStatus.STOPPED

        if output == "running":
            return DeploymentStatus.RUNNING
        else:
            return DeploymentStatus.STOPPED
