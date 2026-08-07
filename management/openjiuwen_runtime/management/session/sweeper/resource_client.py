# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Resource Manager HTTP 客户端（idle_consider）。Resource 未部署时可注入 NoOp。"""

from __future__ import annotations

from typing import Any, Optional, Protocol
from urllib.parse import urljoin

from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)


class HttpClient(Protocol):
    async def post(self, url: str, *, json: Optional[dict] = None) -> Any: ...


class ResourceClient:
    """通知 Resource 考虑回收空闲 Pod。"""

    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str,
        idle_consider_path: str = "/resource/idle_consider",
    ) -> None:
        self._http = http
        self._base_url = (base_url or "").rstrip("/") + "/"
        self._path = idle_consider_path

    async def idle_consider(self, endpoint_id: str, service_id: str = "") -> None:
        url = urljoin(self._base_url, self._path.lstrip("/"))
        payload = {"endpoint_id": endpoint_id, "service_id": service_id}
        try:
            await self._http.post(url, json=payload)
            logger.info("idle_consider ok: endpoint=%s service=%s", endpoint_id, service_id)
        except Exception:
            logger.exception(
                "idle_consider failed: endpoint=%s service=%s url=%s",
                endpoint_id,
                service_id,
                url,
            )


class NoOpResourceClient:
    """未配置 Resource 时使用（仍走 Pass B 去重逻辑，但不发 HTTP）。"""

    async def idle_consider(self, endpoint_id: str, service_id: str = "") -> None:
        logger.debug("idle_consider skipped (noop): endpoint=%s service=%s", endpoint_id, service_id)
