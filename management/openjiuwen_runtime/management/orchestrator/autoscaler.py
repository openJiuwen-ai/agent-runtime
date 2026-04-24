# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""自动伸缩器模块 - 基于负载自动伸缩服务"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

from openjiuwen_runtime.foundation.log import get_logger

if TYPE_CHECKING:
    from .service_handler import ServiceHandler

logger = get_logger(__name__)


class ScalingAction(str, Enum):
    """伸缩动作"""
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    NONE = "none"


@dataclass
class ScalingConfig:
    """伸缩配置"""
    scale_up_threshold: float = 0.8  # 扩容阈值：平均并发度超过 80%
    scale_down_threshold: float = 0.2  # 缩容阈值：平均并发度低于 20%
    cooldown_seconds: int = 60  # 冷却时间（秒）
    min_idle_services: int = 1  # 最小空闲服务数
    max_services: int = 10  # 最大服务数
    scale_down_idle_seconds: int = 120  # 空闲多久后缩容（秒）


@dataclass
class ServiceMetrics:
    """服务指标"""
    deployment_id: str
    state: str
    current_concurrency: int
    max_concurrency: int
    session_count: int
    utilization: float  # 利用率 = current_concurrency / max_concurrency
    idle_seconds: float  # 空闲时间


class AutoScaler:
    """自动伸缩器，基于负载自动伸缩服务"""

    def __init__(self, config: ScalingConfig):
        self._config = config
        self._last_scaling_time: float = 0
        self._service_idle_times: Dict[str, float] = {}  # deployment_id -> 开始空闲时间
        logger.info(
            f"AutoScaler initialized: scale_up_threshold={config.scale_up_threshold}, "
            f"scale_down_threshold={config.scale_down_threshold}, "
            f"cooldown_seconds={config.cooldown_seconds}"
        )

    @property
    def config(self) -> ScalingConfig:
        return self._config

    def collect_metrics(self, services: Dict[str, "ServiceHandler"]) -> List[ServiceMetrics]:
        """
        收集服务指标

        Args:
            services: 服务字典 {deployment_id: ServiceHandler}

        Returns:
            服务指标列表
        """
        metrics = []
        current_time = time.time()

        for deployment_id, handler in services.items():
            utilization = handler.current_concurrency / handler.max_concurrency if handler.max_concurrency > 0 else 0

            idle_seconds = 0.0
            if handler.current_concurrency == 0:
                if deployment_id not in self._service_idle_times:
                    self._service_idle_times[deployment_id] = current_time
                idle_seconds = current_time - self._service_idle_times.get(deployment_id, current_time)
            else:
                self._service_idle_times[deployment_id] = current_time

            metric = ServiceMetrics(
                deployment_id=deployment_id,
                state=handler.state.value,
                current_concurrency=handler.current_concurrency,
                max_concurrency=handler.max_concurrency,
                session_count=len(handler.sessions),
                utilization=utilization,
                idle_seconds=idle_seconds,
            )
            metrics.append(metric)

        return metrics

    def check_scaling(self, metrics: List[ServiceMetrics]) -> tuple[ScalingAction, Optional[str]]:
        """
        检查是否需要伸缩

        Args:
            metrics: 服务指标列表

        Returns:
            (伸缩动作, 目标 deployment_id 或 None)
        """
        if not metrics:
            return ScalingAction.NONE, None

        current_time = time.time()

        if current_time - self._last_scaling_time < self._config.cooldown_seconds:
            logger.debug("In cooldown period, skipping scaling check")
            return ScalingAction.NONE, None

        total_concurrency = sum(m.current_concurrency for m in metrics)
        total_capacity = sum(m.max_concurrency for m in metrics)
        avg_utilization = total_concurrency / total_capacity if total_capacity > 0 else 0

        logger.debug(
            f"Scaling check: avg_utilization={avg_utilization:.2%}, "
            f"total_concurrency={total_concurrency}, total_capacity={total_capacity}"
        )

        if avg_utilization >= self._config.scale_up_threshold:
            if len(metrics) < self._config.max_services:
                logger.info(f"Scale up needed: avg_utilization={avg_utilization:.2%}")
                return ScalingAction.SCALE_UP, None

        idle_services = [m for m in metrics if m.idle_seconds >= self._config.scale_down_idle_seconds]
        if len(idle_services) > self._config.min_idle_services:
            target = idle_services[0]
            logger.info(
                f"Scale down needed: deployment_id='{target.deployment_id}', "
                f"idle_seconds={target.idle_seconds:.1f}s"
            )
            return ScalingAction.SCALE_DOWN, target.deployment_id

        return ScalingAction.NONE, None

    def record_scaling(self) -> None:
        """记录伸缩操作时间"""
        self._last_scaling_time = time.time()
        logger.debug(f"Scaling recorded at {self._last_scaling_time}")

    def get_aggregate_metrics(self, metrics: List[ServiceMetrics]) -> Dict:
        """
        获取聚合指标

        Args:
            metrics: 服务指标列表

        Returns:
            聚合指标字典
        """
        if not metrics:
            return {
                "service_count": 0,
                "total_concurrency": 0,
                "total_capacity": 0,
                "avg_utilization": 0.0,
                "idle_service_count": 0,
            }

        total_concurrency = sum(m.current_concurrency for m in metrics)
        total_capacity = sum(m.max_concurrency for m in metrics)
        idle_count = sum(1 for m in metrics if m.current_concurrency == 0)

        return {
            "service_count": len(metrics),
            "total_concurrency": total_concurrency,
            "total_capacity": total_capacity,
            "avg_utilization": total_concurrency / total_capacity if total_capacity > 0 else 0.0,
            "idle_service_count": idle_count,
        }

    def cleanup_stale_services(self, active_deployment_ids: List[str]) -> None:
        """
        清理已移除服务的空闲时间记录

        Args:
            active_deployment_ids: 当前活跃的服务 ID 列表
        """
        stale_ids = set(self._service_idle_times.keys()) - set(active_deployment_ids)
        for deployment_id in stale_ids:
            del self._service_idle_times[deployment_id]
            logger.debug(f"Cleaned up stale idle time record: deployment_id='{deployment_id}'")
