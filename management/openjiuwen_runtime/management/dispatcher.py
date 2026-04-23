# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Dispatcher 入口类 - 消息分发与调度"""
from abc import ABC
from dataclasses import dataclass
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger

from .session_manager.models import DispatchHeader, DispatchResult

logger = get_logger(__name__)


@dataclass
class DispatcherConfig:
    """Dispatcher 配置类"""

    db_handler: DBHandler
    image: str
    max_concurrency_per_pod: int = 200
    max_instances: int = 10
    namespace: str = "default"
    knative_domain: str = "default.example.com"
    service_name: str = "jiuwen-agent"
    target_port: int = 8000
    invoke_path: str = "/invoke"
    default_ttl: int = 30

class Server(ABC):
    def __init__(self, config: DispatcherConfig):
        self.config = config

    async def dispatch(self, header: DispatchHeader, msg: Any) -> DispatchResult:
        pass