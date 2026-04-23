# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""K8s 部署器"""

import asyncio
import os
from typing import Optional

from openjiuwen_runtime.foundation.log import get_logger

from ..base.deployer import Deployer
from ..base.models import DeployContext, DeployResult
from .models import K8sParams
from ...models.enums import DeploymentStatus

logger = get_logger(__name__)


class K8sDeployer(Deployer[K8sParams]):
    """Kubernetes 部署器"""

    def __init__(
            self,
            default_host: str = "localhost",
            kubeconfig: Optional[str] = None,
            namespace: str = "default",
    ):
        self.default_host = default_host
        self.kubeconfig = kubeconfig
        self.namespace = namespace
        self._deployments: dict[str, str] = {}
        logger.debug("K8sDeployer initialized: namespace=%s", namespace)

    def _get_kubectl_env(self) -> dict:
        env = os.environ.copy()
        if self.kubeconfig:
            env["KUBECONFIG"] = self.kubeconfig
        return env

    async def _run_kubectl_command(self, *args: str) -> tuple[bool, str]:
        cmd = ["kubectl"]
        cmd.extend(args)

        logger.debug("Running kubectl command: %s", " ".join(args))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._get_kubectl_env(),
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            return True, stdout.decode().strip()
        return False, stderr.decode().strip()

    async def deploy(self, ctx: DeployContext[K8sParams]) -> DeployResult:
        logger.info("Deploying k8s: deployment_id=%s, host=%s", ctx.deployment_id, ctx.host)
        try:
            k8s_params = ctx.params or K8sParams()
            namespace = k8s_params.namespace or self.namespace
            deployment_name = k8s_params.deployment_name or f"deploy-{ctx.deployment_id}"
            replicas = k8s_params.replicas or 1
            config_map = k8s_params.config_map
            secret = k8s_params.secret
            image = k8s_params.image
            host = ctx.host or self.default_host

            if config_map:
                logger.debug("Creating config map: namespace=%s", namespace)
                cm_manifest = self._build_config_map_manifest(deployment_name, namespace, config_map)
                success, output = await self._run_kubectl_command("apply", "-f", "-", "--namespace", namespace)
                if not success:
                    logger.error(
                        "ConfigMap creation failed: deployment_id=%s, error=%s",
                        ctx.deployment_id,
                        output,
                    )

            if secret:
                logger.debug("Creating secret: namespace=%s", namespace)
                secret_manifest = self._build_secret_manifest(deployment_name, namespace, secret)
                success, output = await self._run_kubectl_command("apply", "-f", "-", "--namespace", namespace)
                if not success:
                    logger.error(
                        "Secret creation failed: deployment_id=%s, error=%s",
                        ctx.deployment_id,
                        output,
                    )

            deployment_manifest = self._build_deployment_manifest(
                deployment_name, namespace, replicas, ctx.deployment_id, image
            )

            logger.debug("Creating deployment: deployment_name=%s, image=%s", deployment_name, image)
            success, output = await self._run_kubectl_command(
                "apply", "-f", "-", "--namespace", namespace
            )

            if not success:
                logger.error(
                    "kubectl apply failed: deployment_id=%s, error=%s",
                    ctx.deployment_id,
                    output,
                )
                return DeployResult(
                    success=False, message=f"kubectl apply failed: {output}"
                )

            self._deployments[ctx.deployment_id] = deployment_name

            logger.debug("Waiting for rollout: deployment_name=%s", deployment_name)
            success, output = await self._run_kubectl_command(
                "rollout", "status", f"deployment/{deployment_name}", "-n", namespace
            )

            if not success:
                logger.error(
                    "Deployment rollout failed: deployment_id=%s, error=%s",
                    ctx.deployment_id,
                    output,
                )
                return DeployResult(
                    success=False, message=f"Deployment rollout failed: {output}"
                )

            url = f"http://{host}"

            logger.info(
                "K8s deployed: deployment_id=%s, deployment_name=%s, url=%s",
                ctx.deployment_id,
                deployment_name,
                url,
            )
            return DeployResult(
                success=True,
                message="K8s deployment started successfully",
                url=url,
            )

        except Exception as e:
            logger.error("K8s deploy failed: deployment_id=%s, error=%s", ctx.deployment_id, str(e))
            return DeployResult(success=False, message=f"Deployment failed: {str(e)}")

    def _build_deployment_manifest(self, name: str, namespace: str, replicas: int, deployment_id: str, image: Optional[str] = None) -> str:
        image_name = image or f"{deployment_id}:latest"
        return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {namespace}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
      - name: {name}
        image: {image_name}
        ports:
        - containerPort: 8000
"""

    def _build_config_map_manifest(self, name: str, namespace: str, data: dict) -> str:
        import json
        return f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {name}-config
  namespace: {namespace}
data:
  {json.dumps(data, indent=2)}
"""

    def _build_secret_manifest(self, name: str, namespace: str, data: dict) -> str:
        import json
        import base64
        encoded_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
        return f"""
apiVersion: v1
kind: Secret
metadata:
  name: {name}-secret
  namespace: {namespace}
type: Opaque
data:
  {json.dumps(encoded_data, indent=2)}
"""

    async def stop(self, deployment_id: str, **kwargs) -> DeployResult:
        logger.info("Stopping k8s: deployment_id=%s", deployment_id)
        try:
            deployment_name = f"deploy-{deployment_id}"

            logger.debug("Deleting deployment: deployment_name=%s", deployment_name)
            success, output = await self._run_kubectl_command(
                "delete", "deployment", deployment_name, "-n", self.namespace
            )

            if not success:
                logger.error(
                    "kubectl delete failed: deployment_id=%s, error=%s",
                    deployment_id,
                    output,
                )
                return DeployResult(
                    success=False, message=f"kubectl delete failed: {output}"
                )

            if deployment_id in self._deployments:
                del self._deployments[deployment_id]

            logger.info("K8s stopped: deployment_id=%s", deployment_id)
            return DeployResult(
                success=True, message="K8s deployment stopped successfully"
            )

        except Exception as e:
            logger.error("K8s stop failed: deployment_id=%s, error=%s", deployment_id, str(e))
            return DeployResult(success=False, message=f"Stop failed: {str(e)}")

    async def get_status(self, deployment_id: str, **kwargs) -> DeploymentStatus:
        logger.debug("Getting k8s status: deployment_id=%s", deployment_id)
        deployment_name = f"deploy-{deployment_id}"

        success, output = await self._run_kubectl_command(
            "get", "deployment", deployment_name, "-n", self.namespace, "-o",
            "jsonpath={.status.readyReplicas}"
        )

        if not success:
            return DeploymentStatus.STOPPED

        if output and output != "0":
            return DeploymentStatus.RUNNING
        else:
            return DeploymentStatus.PENDING
