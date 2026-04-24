"""K8s deployer implemented with the Kubernetes Python client."""

import asyncio
import errno
import logging
import os
from pathlib import Path
from typing import Any, Optional

from kubernetes import client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

from ..base.deployer import Deployer
from ..base.models import DeployContext, DeployResult
from .models import K8sParams
from ...models.enums import DeploymentStatus

logger = logging.getLogger(__name__)


class K8sDeployer(Deployer[K8sParams]):
    """Kubernetes deployer backed by the official Python client."""

    _WINDOWS_SOCKET_ERROR_MESSAGES = {
        10053: "software caused connection abort",
        10054: "connection reset by peer",
        10060: "connection timed out",
        10061: "connection refused",
    }

    def __init__(
        self,
        default_host: str = "localhost",
        kubeconfig: Optional[str] = None,
        namespace: str = "default",
        rollout_timeout: int = 300,
        rollout_poll_interval: float = 2.0,
    ):
        self.default_host = default_host
        self.kubeconfig = kubeconfig or os.getenv("KUBECONFIG")
        self.namespace = namespace
        self.rollout_timeout = rollout_timeout
        self.rollout_poll_interval = rollout_poll_interval
        self._deployments: dict[str, str] = {}
        self._config_loaded = False
        logger.info(
            "K8sClientDeployer initialized: namespace=%s, kubeconfig_configured=%s",
            namespace,
            bool(self.kubeconfig),
        )

    @staticmethod
    def _default_resource_name(deployment_id: str) -> str:
        return f"agent-{deployment_id[:6]}"

    @staticmethod
    def _normalize_mapping(value: Optional[dict[str, Any]]) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _format_api_exception(exc: ApiException) -> str:
        body = getattr(exc, "body", None)
        if body:
            return f"{exc.status} {exc.reason}: {body}"
        return f"{exc.status} {exc.reason}"

    @classmethod
    def _format_os_error(cls, exc: OSError) -> str:
        error_number = getattr(exc, "errno", None)
        winerror = getattr(exc, "winerror", None)
        message = (
            cls._WINDOWS_SOCKET_ERROR_MESSAGES.get(winerror)
            or cls._WINDOWS_SOCKET_ERROR_MESSAGES.get(error_number)
        )
        if not message:
            code = winerror if winerror is not None else error_number
            if code is not None:
                message = errno.errorcode.get(code, exc.__class__.__name__)
            else:
                message = exc.__class__.__name__

        details = []
        if error_number is not None:
            details.append(f"errno={error_number}")
        if winerror is not None and winerror != error_number:
            details.append(f"winerror={winerror}")
        if details:
            return f"{message} ({', '.join(details)})"
        return message

    @classmethod
    def _format_exception_message(cls, exc: BaseException) -> str:
        if isinstance(exc, ApiException):
            return cls._format_api_exception(exc)
        if isinstance(exc, OSError):
            return cls._format_os_error(exc)

        parts: list[str] = []
        for arg in getattr(exc, "args", ()):
            if isinstance(arg, BaseException):
                formatted = cls._format_exception_message(arg)
            else:
                formatted = str(arg).strip()
            if formatted:
                parts.append(formatted.rstrip("."))

        if parts:
            return ": ".join(parts)

        message = str(exc).strip()
        return message or exc.__class__.__name__

    @staticmethod
    def _is_not_found(exc: ApiException) -> bool:
        return getattr(exc, "status", None) == 404

    @staticmethod
    def _is_conflict(exc: ApiException) -> bool:
        return getattr(exc, "status", None) == 409

    def _get_kubeconfig_host(self) -> Optional[tuple]:
        """从 kubeconfig 的 cluster server URL 中提取主机 IP。"""
        if not self.kubeconfig:
            return None
        try:
            config_path = Path(self.kubeconfig)
            if not config_path.exists():
                return None
            import yaml
            from urllib.parse import urlparse
            with open(config_path, "r", encoding="utf-8") as f:
                kube_conf = yaml.safe_load(f)
            clusters = kube_conf.get("clusters", [])
            if not clusters:
                return None
            server_url = clusters[0].get("cluster", {}).get("server", "")
            parsed = urlparse(server_url)
            return parsed.hostname, parsed.port
        except Exception as exc:
            logger.warning("Failed to parse kubeconfig host: %s", exc)
            return None

    async def _call_api(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    async def _ensure_client_config(self) -> None:
        if self._config_loaded:
            return

        def _load() -> None:
            if self.kubeconfig:
                k8s_config.load_kube_config(config_file=self.kubeconfig)
                return
            try:
                k8s_config.load_incluster_config()
            except ConfigException:
                k8s_config.load_kube_config()

        await asyncio.to_thread(_load)
        self._config_loaded = True

    async def _get_apis(self) -> tuple[client.CoreV1Api, client.AppsV1Api]:
        await self._ensure_client_config()
        return client.CoreV1Api(), client.AppsV1Api()

    def _resolve_ir_file(self, ctx: DeployContext[K8sParams]) -> tuple[str, str]:
        k8s_params = ctx.params or K8sParams()
        ir_path = getattr(k8s_params, "ir_path", None)
        if not ir_path:
            raise RuntimeError("ir_path is required for k8s deployment")

        source_path = Path(ir_path)
        if not source_path.exists():
            raise RuntimeError(f"ir_path not found: {ir_path}")
        return source_path.name, source_path.read_text(encoding="utf-8")

    def _resolve_runtime_settings(
        self, ctx: DeployContext[K8sParams]
    ) -> tuple[str, str, int, int, int, str, Optional[int], dict[str, Any], str, str, Optional[str]]:
        k8s_params = ctx.params or K8sParams()
        deployment_conf = k8s_params.deployment
        service_conf = k8s_params.service

        namespace = k8s_params.namespace or self.namespace
        deployment_name = k8s_params.deployment_name or self._default_resource_name(ctx.deployment_id)
        replicas = deployment_conf.replicas

        container_conf = deployment_conf.container
        image = container_conf.image
        if not image:
            raise RuntimeError("image is required for k8s deployment")
        image_pull_policy = container_conf.image_pull_policy
        container_port = (
            container_conf.container_port
            or ctx.port
        )
        service_port = (
            service_conf.service_port
            or container_port
            or ctx.port
        )
        service_type = service_conf.service_type or "LoadBalancer"
        node_port = service_conf.node_port
        node_selector = self._normalize_mapping(deployment_conf.node_selector)
        userdata = getattr(k8s_params, "userdata", None)

        return (
            namespace,
            deployment_name,
            replicas,
            container_port,
            service_port,
            service_type,
            node_port,
            node_selector,
            image,
            image_pull_policy,
            userdata,
        )

    def _build_secret_body(
        self,
        *,
        name: str,
        namespace: str,
        labels: dict[str, Any],
        config_file_name: str,
        config_content: str,
    ) -> client.V1Secret:
        return client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=client.V1ObjectMeta(
                name=f"{name}-config",
                namespace=namespace,
                labels={key: str(value) for key, value in labels.items()},
            ),
            type="Opaque",
            string_data={config_file_name: config_content},
        )

    def _build_deployment_body(
        self,
        *,
        name: str,
        namespace: str,
        labels: dict[str, Any],
        replicas: int,
        image: str,
        image_pull_policy: str,
        container_port: int,
        config_file_name: str,
        node_selector: Optional[dict[str, Any]],
        env_vars: dict[str, Any],
    ) -> client.V1Deployment:
        container = client.V1Container(
            name=name,
            image=image,
            image_pull_policy=image_pull_policy,
            command=["python"],
            args=[
                "-m",
                "openjiuwen_runtime.examples.lowcode_agent",
                "--irpath",
                f"/app/config/{config_file_name}",
                "--host",
                "0.0.0.0",
                "--port",
                str(container_port),
            ],
            env=[
                client.V1EnvVar(name=key, value=str(value))
                for key, value in env_vars.items()
                if value is not None
            ],
            ports=[
                client.V1ContainerPort(
                    name="http",
                    container_port=container_port,
                )
            ],
            volume_mounts=[
                client.V1VolumeMount(
                    name="agent-config",
                    mount_path="/app/config",
                    read_only=True,
                )
            ],
            startup_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/health", port="http"),
                failure_threshold=30,
                period_seconds=10,
            ),
            readiness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/health", port="http"),
                initial_delay_seconds=5,
                period_seconds=10,
            ),
            liveness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path="/health", port="http"),
                initial_delay_seconds=30,
                period_seconds=20,
            ),
        )

        pod_spec = client.V1PodSpec(
            node_selector={key: str(value) for key, value in (node_selector or {}).items()} or None,
            containers=[container],
            volumes=[
                client.V1Volume(
                    name="agent-config",
                    secret=client.V1SecretVolumeSource(
                        secret_name=f"{name}-config",
                        items=[
                            client.V1KeyToPath(
                                key=config_file_name,
                                path=config_file_name,
                            )
                        ],
                    ),
                )
            ],
        )

        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={key: str(value) for key, value in labels.items()}),
            spec=pod_spec,
        )

        spec = client.V1DeploymentSpec(
            replicas=replicas,
            selector=client.V1LabelSelector(match_labels={"app": name}),
            template=template,
        )

        return client.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={key: str(value) for key, value in labels.items()},
            ),
            spec=spec,
        )

    def _build_service_body(
        self,
        *,
        name: str,
        namespace: str,
        labels: dict[str, Any],
        service_type: str,
        service_port: int,
        node_port: Optional[int],
    ) -> client.V1Service:
        service_port_body = client.V1ServicePort(
            name="http",
            protocol="TCP",
            port=service_port,
            target_port="http",
            node_port=int(node_port) if node_port is not None else None,
        )
        spec = client.V1ServiceSpec(
            type=service_type,
            selector={"app": name},
            ports=[service_port_body],
        )
        return client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={key: str(value) for key, value in labels.items()},
            ),
            spec=spec,
        )

    async def _create_or_patch_secret(
        self, core_api: client.CoreV1Api, namespace: str, body: client.V1Secret
    ):
        try:
            return await self._call_api(core_api.create_namespaced_secret, namespace=namespace, body=body)
        except ApiException as exc:
            if not self._is_conflict(exc):
                raise
            return await self._call_api(
                core_api.patch_namespaced_secret,
                name=body.metadata.name,
                namespace=namespace,
                body=body,
            )

    async def _create_or_patch_deployment(
        self, apps_api: client.AppsV1Api, namespace: str, body: client.V1Deployment
    ):
        try:
            return await self._call_api(apps_api.create_namespaced_deployment, namespace=namespace, body=body)
        except ApiException as exc:
            if not self._is_conflict(exc):
                raise
            return await self._call_api(
                apps_api.patch_namespaced_deployment,
                name=body.metadata.name,
                namespace=namespace,
                body=body,
            )

    async def _create_or_patch_service(
        self, core_api: client.CoreV1Api, namespace: str, body: client.V1Service
    ):
        try:
            return await self._call_api(core_api.create_namespaced_service, namespace=namespace, body=body)
        except ApiException as exc:
            if not self._is_conflict(exc):
                raise
            return await self._call_api(
                core_api.patch_namespaced_service,
                name=body.metadata.name,
                namespace=namespace,
                body=body,
            )

    async def _wait_for_rollout(
        self, apps_api: client.AppsV1Api, namespace: str, deployment_name: str, replicas: int
    ) -> tuple[bool, str]:
        deadline = asyncio.get_running_loop().time() + self.rollout_timeout
        while True:
            deployment = await self._call_api(
                apps_api.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            )
            status = deployment.status or client.V1DeploymentStatus()
            generation = deployment.metadata.generation or 0
            observed_generation = status.observed_generation or 0
            ready_replicas = status.ready_replicas or 0
            available_replicas = status.available_replicas or 0

            if (
                observed_generation >= generation
                and ready_replicas >= replicas
                and available_replicas >= replicas
            ):
                return True, f"deployment/{deployment_name} successfully rolled out"

            if asyncio.get_running_loop().time() >= deadline:
                return False, f"Timed out waiting for deployment/{deployment_name} rollout"
            await asyncio.sleep(self.rollout_poll_interval)

    async def _wait_for_deployment_deleted(
        self,
        apps_api: client.AppsV1Api,
        namespace: str,
        deployment_name: str,
        timeout: int,
    ) -> tuple[bool, str]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                await self._call_api(
                    apps_api.read_namespaced_deployment,
                    name=deployment_name,
                    namespace=namespace,
                )
            except ApiException as exc:
                if self._is_not_found(exc):
                    return True, f"deployment/{deployment_name} deleted"
                return False, self._format_api_exception(exc)

            if asyncio.get_running_loop().time() >= deadline:
                return False, f"Timed out waiting for deployment/{deployment_name} deletion"
            await asyncio.sleep(self.rollout_poll_interval)

    async def _wait_for_pods_deleted(
        self,
        core_api: client.CoreV1Api,
        namespace: str,
        deployment_name: str,
        timeout: int,
    ) -> tuple[bool, str]:
        deadline = asyncio.get_running_loop().time() + timeout
        label_selector = f"app={deployment_name}"
        while True:
            pod_list = await self._call_api(
                core_api.list_namespaced_pod,
                namespace=namespace,
                label_selector=label_selector,
            )
            if not pod_list.items:
                return True, f"pods for {deployment_name} deleted"

            if asyncio.get_running_loop().time() >= deadline:
                pod_names = ",".join(
                    pod.metadata.name for pod in pod_list.items if pod.metadata and pod.metadata.name
                )
                return False, f"Timed out waiting for pods deletion: {pod_names}"
            await asyncio.sleep(self.rollout_poll_interval)

    async def deploy(self, ctx: DeployContext[K8sParams]) -> DeployResult:
        deployment_id = ctx.deployment_id
        logger.info("Deploying k8s with client: deployment_id=%s, host=%s", deployment_id, ctx.host)
        try:
            (
                namespace,
                deployment_name,
                replicas,
                container_port,
                service_port,
                service_type,
                node_port,
                node_selector,
                image,
                image_pull_policy,
                userdata,
            ) = self._resolve_runtime_settings(ctx)

            config_file_name, config_content = self._resolve_ir_file(ctx)

            labels = {
                "app": deployment_name,
                "openjiuwen/deployment-id": deployment_id,
                "openjiuwen/type": "pod",
            }
            env_vars = {
                "DEPLOYMENT_ID": deployment_id,
                "AGENT_CONFIG_FILE": f"/app/config/{config_file_name}",
                "CONTAINER_PORT": str(container_port),
            }
            if userdata:
                env_vars["RUNTIME_USERDATA"] = userdata

            core_api, apps_api = await self._get_apis()
            secret_body = self._build_secret_body(
                name=deployment_name,
                namespace=namespace,
                labels=labels,
                config_file_name=config_file_name,
                config_content=config_content,
            )
            deployment_body = self._build_deployment_body(
                name=deployment_name,
                namespace=namespace,
                labels=labels,
                replicas=replicas,
                image=image,
                image_pull_policy=image_pull_policy,
                container_port=container_port,
                config_file_name=config_file_name,
                node_selector=node_selector,
                env_vars=env_vars,
            )
            service_body = self._build_service_body(
                name=deployment_name,
                namespace=namespace,
                labels=labels,
                service_type=service_type,
                service_port=service_port,
                node_port=node_port,
            )

            await self._create_or_patch_secret(core_api, namespace, secret_body)
            await self._create_or_patch_deployment(apps_api, namespace, deployment_body)
            await self._create_or_patch_service(core_api, namespace, service_body)

            self._deployments[deployment_id] = deployment_name
            success, output = await self._wait_for_rollout(apps_api, namespace, deployment_name, replicas)
            if not success:
                return DeployResult(
                    success=False,
                    deployment_id=deployment_id,
                    message=f"Deployment rollout failed: {output}",
                )

            url = ctx.url
            if not url:
                host = ctx.host
                kube_host, kube_port = self._get_kubeconfig_host() or (None, None)
                if not host or host in {"localhost", "127.0.0.1"}:
                    host = kube_host or "127.0.0.1"
                if node_port is not None:
                    expose_port = int(node_port)
                elif kube_port is not None:
                    expose_port = int(kube_port)
                else:
                    expose_port = service_port
                url = f"http://{host}:{expose_port}"
            elif not url.startswith(("http://", "https://")):
                url = f"http://{url}"

            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message="K8s deployment started successfully",
                url=url,
            )
        except ApiException as exc:
            message = self._format_api_exception(exc)
            logger.error("K8s deploy failed: deployment_id=%s, error=%s", deployment_id, message)
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Deployment failed: {message}",
            )
        except Exception as exc:
            message = self._format_exception_message(exc)
            logger.error("K8s deploy failed: deployment_id=%s, error=%s", deployment_id, message)
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Deployment failed: {message}",
            )

    async def stop(self, deployment_id: str, **kwargs) -> DeployResult:
        logger.info("Stopping k8s with client: deployment_id=%s", deployment_id)
        namespace = kwargs.get("namespace") or self.namespace
        deployment_name = kwargs.get("deployment_name") or self._default_resource_name(deployment_id)
        timeout = int(kwargs.get("timeout") or 60)
        grace_period = int(kwargs.get("grace_period_seconds") or 30)
        try:
            core_api, apps_api = await self._get_apis()

            try:
                await self._call_api(
                    apps_api.delete_namespaced_deployment,
                    name=deployment_name,
                    namespace=namespace,
                    body=client.V1DeleteOptions(
                        grace_period_seconds=grace_period,
                        propagation_policy="Foreground",
                    ),
                )
                logger.info(
                    "deployment/%s deletion initiated with %ss grace period",
                    deployment_name,
                    grace_period,
                )
            except ApiException as exc:
                if not self._is_not_found(exc):
                    raise
                logger.warning("deployment/%s not found", deployment_name)

            deployment_deleted, deployment_message = await self._wait_for_deployment_deleted(
                apps_api,
                namespace,
                deployment_name,
                timeout,
            )
            if not deployment_deleted:
                return DeployResult(
                    success=False,
                    deployment_id=deployment_id,
                    message=f"Stop failed: {deployment_message}",
                )

            pods_deleted, pods_message = await self._wait_for_pods_deleted(
                core_api,
                namespace,
                deployment_name,
                timeout,
            )
            if not pods_deleted:
                return DeployResult(
                    success=False,
                    deployment_id=deployment_id,
                    message=f"Stop failed: {pods_message}",
                )

            for resource_name, delete_call in [
                (
                    f"service/{deployment_name}",
                    lambda: core_api.delete_namespaced_service(
                        name=deployment_name,
                        namespace=namespace,
                        body=client.V1DeleteOptions(),
                    ),
                ),
                (
                    f"secret/{deployment_name}-config",
                    lambda: core_api.delete_namespaced_secret(
                        name=f"{deployment_name}-config",
                        namespace=namespace,
                        body=client.V1DeleteOptions(),
                    ),
                ),
            ]:
                try:
                    await self._call_api(delete_call)
                    logger.info("%s deletion initiated", resource_name)
                except ApiException as exc:
                    if not self._is_not_found(exc):
                        raise
                    logger.warning("%s not found", resource_name)

            self._deployments.pop(deployment_id, None)
            return DeployResult(
                success=True,
                deployment_id=deployment_id,
                message=f"K8s deployment stopped successfully: {deployment_message}; {pods_message}",
            )
        except ApiException as exc:
            message = self._format_api_exception(exc)
            logger.error("K8s stop failed: deployment_id=%s, error=%s", deployment_id, message)
            return DeployResult(
                success=False,
                deployment_id=deployment_id,
                message=f"Stop failed: {message}",
            )

    async def get_status(self, deployment_id: str, **kwargs) -> DeploymentStatus:
        """获取 K8s 部署状态（通过 Pod phase 判断）

        Args:
            deployment_id: 部署ID
            **kwargs: namespace, deployment_name

        Returns:
            DeploymentStatus: 部署状态
        """
        namespace = kwargs.get("namespace") or self.namespace
        deployment_name = (
            kwargs.get("deployment_name")
            or self._deployments.get(deployment_id)
            or self._default_resource_name(deployment_id)
        )
        logger.debug("Getting k8s status: deployment_id=%s, deployment_name=%s", deployment_id, deployment_name)

        try:
            core_api, _ = await self._get_apis()
            pod_list = await self._call_api(
                core_api.list_namespaced_pod,
                namespace=namespace,
                label_selector=f"app={deployment_name}",
            )
        except ApiException as exc:
            logger.warning("Failed to list pods for %s: %s", deployment_name, self._format_api_exception(exc))
            return DeploymentStatus.PENDING

        if not pod_list.items:
            return DeploymentStatus.STOPPED

        has_running_not_ready = False
        for pod in pod_list.items:
            pod_info = pod.to_dict() if pod else None
            if not pod_info or "status" not in pod_info:
                continue
            phase = (pod_info["status"].get("phase") or "").lower()
            if phase in ("failed", "succeeded"):
                return DeploymentStatus.STOPPED
            if phase == "running":
                container_statuses = pod_info["status"].get("container_statuses") or []
                all_ready = all(cs.get("ready", False) for cs in container_statuses) and container_statuses
                if all_ready:
                    return DeploymentStatus.RUNNING
                has_running_not_ready = True

        if has_running_not_ready:
            return DeploymentStatus.RUNNING_NOTREADY
        return DeploymentStatus.PENDING
