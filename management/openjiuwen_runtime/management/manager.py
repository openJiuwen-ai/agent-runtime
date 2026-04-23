# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse, urlunparse

from openjiuwen_runtime.foundation.log import get_logger
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.table_def import TableDefinition, ColumnDefinition, IndexDefinition
from .deployments import (
    BaseDeploymentStrategy,
    ProcessInfo,
    DockerInfo,
    K8sInfo,
    SubprocessStrategy,
    DockerStrategy,
    K8sStrategy,
)
from .models.deployment_params import (
    DeployAgentParams,
    DeployPluginParams,
    DeployImageParams,
    ListDeploymentsParams,
)
from .models.enums import DeployMode, DeploymentType, DeploymentStatus
from .models.schemas import (
    DeploymentInfo,
    DeploymentCreate,
    DEPLOYMENT_TABLE_NAME,
    DeploymentFields,
)

logger = get_logger(__name__)


@dataclass
class _DeployExecutionParams:
    """内部统一部署流程参数（由 deploy_agent / deploy_plugin 组装）"""

    deployment_type: DeploymentType
    name: str
    version: str
    mode: DeployMode
    user_id: Optional[str] = None
    space_id: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)


DEPLOYMENT_TABLE_DEF = TableDefinition(
    table_name=DEPLOYMENT_TABLE_NAME,
    columns=[
        ColumnDefinition(DeploymentFields.ID, "integer", primary_key=True, autoincrement=True),
        ColumnDefinition(DeploymentFields.DEPLOYMENT_ID, "string", length=64, unique=True, nullable=False),
        ColumnDefinition(DeploymentFields.VERSION, "string", length=32, nullable=False),
        ColumnDefinition(DeploymentFields.DEPLOYMENT_TYPE, "string", length=32, nullable=False),
        ColumnDefinition(DeploymentFields.DEPLOYMENT_STATUS, "string", length=32, nullable=False),
        ColumnDefinition(DeploymentFields.NAME, "string", length=255, nullable=False),
        ColumnDefinition(DeploymentFields.URL, "string", length=512, nullable=True),
        ColumnDefinition(DeploymentFields.USER_ID, "string", length=64, nullable=True),
        ColumnDefinition(DeploymentFields.SPACE_ID, "string", length=64, nullable=True),
        ColumnDefinition(DeploymentFields.CREATED_AT, "datetime", nullable=False),
        ColumnDefinition(DeploymentFields.UPDATED_AT, "datetime", nullable=False),
        ColumnDefinition(DeploymentFields.DATA, "json", nullable=True),
    ],
    indexes=[
        IndexDefinition([DeploymentFields.DEPLOYMENT_ID], unique=True),
        IndexDefinition([DeploymentFields.USER_ID], unique=False),
        IndexDefinition([DeploymentFields.SPACE_ID], unique=False),
    ],
)


class DeploymentManager:
    """部署管理器"""

    def __init__(
            self,
            db_handler: DBHandler,
            strategies: Optional[dict[DeployMode, BaseDeploymentStrategy]] = None,
    ):
        self.db_handler = db_handler
        self._strategies = strategies or self._create_default_strategies()
        logger.debug("DeploymentManager initialized")

    @staticmethod
    def _create_default_strategies() -> dict[DeployMode, BaseDeploymentStrategy]:
        """创建默认策略"""
        return {
            DeployMode.SUBPROCESS: SubprocessStrategy(),
            DeployMode.DOCKER: DockerStrategy(),
            DeployMode.K8S: K8sStrategy(),
        }

    @staticmethod
    def _generate_deployment_id() -> str:
        """生成部署ID"""
        return str(uuid.uuid4())

    @staticmethod
    def _health_url_with_loopback_host(base_url: str) -> Optional[str]:
        """
        将 base_url 的主机替换为 127.0.0.1，用于配置 IP 被本机访问策略拦截时的探活兜底。
        若已是环回地址或无法解析主机，则返回 None。
        """
        parsed = urlparse(base_url.rstrip("/") + "/")
        host = parsed.hostname
        if not host:
            return None
        if host in ("127.0.0.1", "localhost", "::1"):
            return None
        port = parsed.port
        netloc = f"127.0.0.1:{port}" if port is not None else "127.0.0.1"
        replaced = urlunparse(
            (parsed.scheme, netloc, parsed.path.rstrip("/") or "", "", "", "")
        ).rstrip("/")
        # URL 路径按 POSIX 语义拼接，使用 urljoin 避免手写分隔符
        return urljoin(replaced.rstrip("/") + "/", "health")

    async def initialize(self) -> None:
        """初始化管理器"""
        logger.info("Initializing DeploymentManager")
        await self.db_handler.connect()
        await self.db_handler.init_table(DEPLOYMENT_TABLE_DEF)
        for strategy in self._strategies.values():
            await self.db_handler.init_table(strategy.get_table_definition())
        logger.info("DeploymentManager initialized successfully")

    async def shutdown(self) -> None:
        """关闭管理器"""
        logger.info("Shutting down DeploymentManager")
        await self.db_handler.disconnect()
        logger.info("DeploymentManager shutdown complete")
    
    async def deploy_agent(self, params: DeployAgentParams) -> DeploymentInfo:
        """部署Agent"""
        logger.info(
            "Deploying agent: name=%s, version=%s, mode=%s, user_id=%s, space_id=%s",
            params.name,
            params.version,
            params.mode,
            params.user_id,
            params.space_id,
        )
        extras = dict(params.extras)
        extras["package_name"] = params.name
        return await self._deploy(
            _DeployExecutionParams(
                deployment_type=DeploymentType.AGENT,
                name=params.name,
                version=params.version,
                mode=params.mode,
                user_id=params.user_id,
                space_id=params.space_id,
                extras=extras,
            )
        )

    async def deploy_plugin(self, params: DeployPluginParams) -> DeploymentInfo:
        """部署Plugin"""
        logger.info(
            "Deploying plugin: name=%s, version=%s, mode=%s, user_id=%s, space_id=%s",
            params.name,
            params.version,
            params.mode,
            params.user_id,
            params.space_id,
        )
        extras = dict(params.extras)
        extras["url"] = params.url
        return await self._deploy(
            _DeployExecutionParams(
                deployment_type=DeploymentType.PLUGIN,
                name=params.name,
                version=params.version,
                mode=params.mode,
                user_id=params.user_id,
                space_id=params.space_id,
                extras=extras,
            )
        )

    async def deploy_image(self, params: DeployImageParams) -> DeploymentInfo:
        """部署镜像"""
        if params.mode == DeployMode.SUBPROCESS:
            raise NotImplementedError("deploy_image is not supported for SUBPROCESS mode")
        logger.info(
            "Deploying image: name=%s, version=%s, mode=%s, user_id=%s, space_id=%s",
            params.name,
            params.version,
            params.mode,
            params.user_id,
            params.space_id,
        )
        extras = dict(params.extras)
        extras["image"] = params.image
        return await self._deploy(
            _DeployExecutionParams(
                deployment_type=DeploymentType.IMAGE,
                name=params.name,
                version=params.version,
                mode=params.mode,
                user_id=params.user_id,
                space_id=params.space_id,
                extras=extras,
            )
        )

    async def list_deployments(self, params: ListDeploymentsParams) -> list[DeploymentInfo]:
        """列出部署"""
        logger.debug(
            "Listing deployments: type=%s, status=%s, user_id=%s, space_id=%s, limit=%s, offset=%s",
            params.deployment_type,
            params.deployment_status,
            params.user_id,
            params.space_id,
            params.limit,
            params.offset,
        )
        filters = {}
        if params.deployment_type:
            filters[DeploymentFields.DEPLOYMENT_TYPE] = params.deployment_type.value
        if params.deployment_status:
            filters[DeploymentFields.DEPLOYMENT_STATUS] = params.deployment_status.value
        if params.user_id:
            filters[DeploymentFields.USER_ID] = params.user_id
        if params.space_id:
            filters[DeploymentFields.SPACE_ID] = params.space_id

        records = await self.db_handler.list_records(
            DEPLOYMENT_TABLE_NAME,
            filters=filters if filters else None,
            limit=params.limit,
            offset=params.offset,
        )
        result = [DeploymentInfo.model_validate(r if hasattr(r, "to_dict") else r) for r in records]
        logger.debug("Found %s deployments", len(result))
        return result

    async def get_deployment(self, deployment_id: str) -> Optional[DeploymentInfo]:
        """获取部署详情"""
        logger.debug("Getting deployment: deployment_id=%s", deployment_id)
        record = await self.db_handler.get(
            DEPLOYMENT_TABLE_NAME, 
            {DeploymentFields.DEPLOYMENT_ID: deployment_id}
        )
        if not record:
            logger.debug("Deployment not found: deployment_id=%s", deployment_id)
            return None
        if hasattr(record, "to_dict"):
            return DeploymentInfo.model_validate(record.to_dict())
        return DeploymentInfo.model_validate(record)

    async def get_deployment_status(self, deployment_id: str) -> Optional[DeploymentStatus]:
        """获取部署状态"""
        logger.debug("Getting deployment status: deployment_id=%s", deployment_id)
        record = await self.db_handler.get(
            DEPLOYMENT_TABLE_NAME, 
            {DeploymentFields.DEPLOYMENT_ID: deployment_id}
        )
        if not record:
            return None
        status = record.get(DeploymentFields.DEPLOYMENT_STATUS) \
            if isinstance(record, dict) else record.deployment_status
        return DeploymentStatus(status)

    async def stop_deployment(
            self, deployment_id: str, mode: Optional[DeployMode] = None
    ) -> bool:
        """停止部署"""
        logger.info("Stopping deployment: deployment_id=%s", deployment_id)
        if mode is None:
            mode = await self._detect_deploy_mode(deployment_id)
            if mode is None:
                logger.warning("Cannot stop deployment, mode not found: deployment_id=%s", deployment_id)
                return False

        strategy = self._get_strategy(mode)
        result = await strategy.stop(deployment_id, self.db_handler)
        if result.success:
            logger.info("Deployment stopped: deployment_id=%s", deployment_id)
        else:
            logger.error(
                "Failed to stop deployment: deployment_id=%s, message=%s",
                deployment_id,
                result.message,
            )
        return result.success

    async def delete_deployment(
            self, deployment_id: str, mode: Optional[DeployMode] = None
    ) -> bool:
        """删除部署"""
        logger.info("Deleting deployment: deployment_id=%s", deployment_id)
        if mode is None:
            mode = await self._detect_deploy_mode(deployment_id)

        await self.stop_deployment(deployment_id, mode)

        if mode:
            strategy = self._get_strategy(mode)
            await strategy.delete_record(self.db_handler, deployment_id)

        result = await self.db_handler.delete(
            DEPLOYMENT_TABLE_NAME, 
            {DeploymentFields.DEPLOYMENT_ID: deployment_id}
        )
        if result:
            logger.info("Deployment deleted: deployment_id=%s", deployment_id)
        else:
            logger.error("Failed to delete deployment: deployment_id=%s", deployment_id)
        return result

    async def get_process_info(self, deployment_id: str) -> Optional[ProcessInfo]:
        """获取进程部署详情"""
        logger.debug("Getting process info: deployment_id=%s", deployment_id)
        strategy = self._get_strategy(DeployMode.SUBPROCESS)
        return await strategy.get_info(self.db_handler, deployment_id)

    async def get_docker_info(self, deployment_id: str) -> Optional[DockerInfo]:
        """获取Docker部署详情"""
        logger.debug("Getting docker info: deployment_id=%s", deployment_id)
        strategy = self._get_strategy(DeployMode.DOCKER)
        return await strategy.get_info(self.db_handler, deployment_id)

    async def get_k8s_info(self, deployment_id: str) -> Optional[K8sInfo]:
        """获取K8S部署详情"""
        logger.debug("Getting k8s info: deployment_id=%s", deployment_id)
        strategy = self._get_strategy(DeployMode.K8S)
        return await strategy.get_info(self.db_handler, deployment_id)

    def _get_strategy(self, mode: DeployMode) -> BaseDeploymentStrategy:
        """获取部署策略"""
        return self._strategies[mode]

    async def _detect_deploy_mode(self, deployment_id: str) -> Optional[DeployMode]:
        """检测部署模式"""
        logger.debug("Detecting deploy mode for deployment_id=%s", deployment_id)
        for mode, strategy in self._strategies.items():
            record = await strategy.get_record(self.db_handler, deployment_id)
            if record:
                logger.debug("Detected deploy mode: %s for deployment_id=%s", mode, deployment_id)
                return mode
        logger.debug("No deploy mode found for deployment_id=%s", deployment_id)
        return None

    async def _check_health_endpoint(self, base_url: str, timeout_seconds: float = 2.0) -> bool:
        """检查部署服务 /health 是否可访问；主 URL 失败时再尝试 127.0.0.1 同端口兜底。"""
        url = urljoin(base_url.rstrip("/") + "/", "health")

        def _probe(target: str) -> bool:
            req = urllib_request.Request(target, method="GET")
            with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
                return 200 <= resp.status < 300

        async def _try_once(target: str) -> bool:
            try:
                return await asyncio.to_thread(_probe, target)
            except OSError:
                return False

        if await _try_once(url):
            return True
        fallback = self._health_url_with_loopback_host(base_url)
        if fallback and fallback != url:
            return await _try_once(fallback)
        return False

    async def _wait_until_deployment_ready(
        self,
        deployment_id: str,
        timeout_seconds: int = 600,
        interval_seconds: float = 20.0,
    ) -> None:
        """
        等待部署就绪：
        1) 状态为 running
        2) 若存在 URL，则 /health 探活成功
        """
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_status = None

        while asyncio.get_running_loop().time() < deadline:
            deployment = await self.get_deployment(deployment_id)
            if deployment is None:
                raise RuntimeError(f"Deployment {deployment_id} not found while waiting for readiness")

            last_status = deployment.deployment_status.value
            if deployment.deployment_status == DeploymentStatus.RUNNING:
                if deployment.url:
                    if await self._check_health_endpoint(deployment.url):
                        return
                else:
                    return

            if deployment.deployment_status == DeploymentStatus.FAILED:
                raise RuntimeError(f"Deployment {deployment_id} entered failed status")

            await asyncio.sleep(interval_seconds)

        raise RuntimeError(
            f"Deployment {deployment_id} not ready within {timeout_seconds}s (last_status={last_status})"
        )

    async def _deploy(self, params: _DeployExecutionParams) -> DeploymentInfo:
        """内部部署方法"""
        extras = dict(params.extras)
        deployment_id = extras.pop("deployment_id", None) or self._generate_deployment_id()
        now = datetime.utcnow()

        logger.debug(
            "deployment_id=%s, type=%s, name=%s",
            deployment_id,
            params.deployment_type,
            params.name,
        )

        create_model = DeploymentCreate(
            deployment_id=deployment_id,
            version=params.version,
            deployment_type=params.deployment_type,
            name=params.name,
            url=extras.get("url"),
            user_id=params.user_id,
            space_id=params.space_id,
            data=extras.get("data"),
        )
        deployment_data = create_model.model_dump()
        deployment_data[DeploymentFields.DEPLOYMENT_STATUS] = DeploymentStatus.PENDING.value
        deployment_data[DeploymentFields.CREATED_AT] = now
        deployment_data[DeploymentFields.UPDATED_AT] = now

        await self.db_handler.create(DEPLOYMENT_TABLE_NAME, deployment_data)

        strategy = self._get_strategy(params.mode)

        try:
            await strategy.create_record(
                self.db_handler, deployment_id, params.version, **extras
            )
            await strategy.deploy(deployment_id, self.db_handler)

            await self._wait_until_deployment_ready(deployment_id)
            logger.info(
                "Deployment completed: deployment_id=%s, name=%s",
                deployment_id,
                params.name,
            )
        except Exception as e:
            logger.error("Deployment failed: deployment_id=%s, error=%s", deployment_id, str(e))
            await self.db_handler.update(
                DEPLOYMENT_TABLE_NAME,
                {DeploymentFields.DEPLOYMENT_ID: deployment_id},
                {DeploymentFields.DEPLOYMENT_STATUS: DeploymentStatus.FAILED.value},
            )

        deployment_record = await self.db_handler.get(
            DEPLOYMENT_TABLE_NAME,
            {DeploymentFields.DEPLOYMENT_ID: deployment_id},
        )
        return DeploymentInfo.model_validate(deployment_record)
