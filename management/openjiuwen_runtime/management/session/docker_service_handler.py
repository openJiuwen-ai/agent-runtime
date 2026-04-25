# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Docker 容器级部署：提供与 K8s 同构的 deploy/delete。生产可接 docker engine API；无依赖环境用 Mock。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen_runtime.foundation.log import get_logger

from .runtime import IDeployController

logger = get_logger(__name__)


@dataclass(frozen=True)
class DockerRunInfo:
    """容器拉起后的可访问点。"""

    container_id: str
    host: str
    port: int


class DockerServiceHandler:
    """占位实现：不强制依赖 docker 库；可替换为 aiodocker 等。"""

    def __init__(
        self,
        image: str,
        *,
        host: str = "127.0.0.1",
        publish_port: int = 8000,
    ) -> None:
        if not image:
            raise ValueError("image is required")
        self._image = image
        self._host = host
        self._port = int(publish_port)
        self._container_id: Optional[str] = None

    @property
    def container_id(self) -> Optional[str]:
        return self._container_id

    async def deploy(self) -> DockerRunInfo:
        # 可替换为实际 docker run + 等待健康检查
        self._container_id = f"ctr-{uuid.uuid4().hex[:12]}"
        info = DockerRunInfo(container_id=self._container_id, host=self._host, port=self._port)
        logger.info("Docker deploy (stub): %s", info)
        return info

    async def delete(self) -> str:
        cid = self._container_id or "unknown"
        self._container_id = None
        logger.info("Docker delete (stub): %s", cid)
        return cid


class DockerDeployController:
    def __init__(self, inner: DockerServiceHandler) -> None:
        self._inner = inner

    @property
    def resource_id(self) -> str | None:
        return self._inner.container_id

    async def deploy(self) -> Any:
        return await self._inner.deploy()

    async def delete(self) -> str:
        return await self._inner.delete()
