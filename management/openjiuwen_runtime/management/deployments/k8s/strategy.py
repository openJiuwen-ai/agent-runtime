# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""K8s 部署策略"""

from datetime import datetime
from typing import Any

from .deployer import K8sDeployer
from .models import K8sInfo, K8sParams, K8S_TABLE_DEF
from ..base.models import DeployContext, CommonParams
from ..base.strategy import BaseDeploymentStrategy


class K8sStrategy(BaseDeploymentStrategy[K8sInfo]):
    """K8s 部署策略"""

    def _create_default_deployer(self) -> K8sDeployer:
        return K8sDeployer()

    def _create_table_definition(self):
        return K8S_TABLE_DEF

    def _get_info_class(self) -> type:
        return K8sInfo

    def _build_record_data(
        self, deployment_id: str, version: str, **kwargs: Any
    ) -> dict:
        now = datetime.utcnow()
        return {
            "deployment_id": deployment_id,
            "version": version,
            "host": kwargs.get("host", "localhost"),
            "url": kwargs.get("url"),
            "pid": kwargs.get("pid"),
            "whl_path": kwargs.get("whl_path"),
            "package_name": kwargs.get("package_name"),
            "template_file": kwargs.get("template_file"),
            "image": kwargs.get("image"),
            "namespace": kwargs.get("namespace"),
            "deployment_name": kwargs.get("deployment_name"),
            "replicas": kwargs.get("replicas"),
            "created_at": now,
            "updated_at": now,
            "data": kwargs.get("data"),
        }

    def _build_deploy_context(self, record: Any, deployment: Any) -> DeployContext[K8sParams]:
        if hasattr(record, "to_dict"):
            data = record.to_dict()
        else:
            data = record
        return DeployContext(
            common=CommonParams(
                deployment_id=data.get("deployment_id"),
                host=data.get("host"),
                url=data.get("url"),
            ),
            params=K8sParams(
                namespace=data.get("namespace"),
                deployment_name=data.get("deployment_name"),
                replicas=data.get("replicas"),
                config_map=data.get("config_map"),
                secret=data.get("secret"),
                image=data.get("image"),
            ),
            data=data.get("data"),
        )

    def _get_stop_kwargs(self, record: Any) -> dict:
        return {}

    def _get_status_kwargs(self, record: Any) -> dict:
        return {}
