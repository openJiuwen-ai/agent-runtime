# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Orchestrator 数据模型"""

from enum import Enum

from openjiuwen_runtime.foundation.db import DBHandler


class MessagePriority(str, Enum):
    """消息优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AccessConfig:
    """Access 配置类"""

    db_handler: DBHandler  # db存储
    image: str  # 镜像名
    max_concurrency: int = 200  # 服务的最大并发
    min_idle_services: int = 1  # 服务的最小空闲数
    max_services: int = 10  # 服务最大数
    target_port: int = 8000  # 目标端口
    invoke_path: str = "/invoke"  # uri
    service_ttl: int = 300  # 服务ttl
    queue_size: int = 100  # 队列大小
    message_timeout: int = 30  # 消息处理超时（秒）
    max_retries: int = 3  # 最大重试次数


class ServiceState(str, Enum):
    """服务状态"""
    INITIALIZING = "initializing"
    RUNNING = "running"
    IDLE = "idle"
    TERMINATING = "terminating"


class SessionState(str, Enum):
    """会话状态"""
    IDLE = "idle"
    RUNNING = "running"


class SessionConfig:
    """Session 级别配置"""

    concurrency: int = 1  # 单 session 并发度
    ttl: int = 300  # 单 session 生存时间（秒）
