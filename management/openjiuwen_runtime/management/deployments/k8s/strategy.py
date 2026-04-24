"""K8s 部署策略"""

import json
import re
from datetime import datetime
from typing import Any

from ..base.models import DeployContext, CommonParams
from ..base.strategy import BaseDeploymentStrategy
from .deployer import K8sDeployer
from .models import (
    K8sInfo, K8sParams, K8sContainer, K8sDeployment, K8sService, K8S_TABLE_DEF,
)

from openjiuwen_runtime.foundation.config import settings

import logging
logger = logging.getLogger(__name__)


def _parse_userdata(raw_data: Any) -> dict:
    """从 data 字段中解析 userdata，返回 k8s 配置字典。"""
    def _loads_dict(raw_json: str) -> dict:
        parsed = json.loads(raw_json)
        return parsed if isinstance(parsed, dict) else {}

    if not isinstance(raw_data, dict):
        return {}
    userdata = raw_data.get("userdata")
    if userdata is None:
        return {}
    if isinstance(userdata, dict):
        return userdata
    if isinstance(userdata, str):
        try:
            return _loads_dict(userdata)
        except (json.JSONDecodeError, TypeError):
            sanitized = re.sub(r",\s*([}\]])", r"\1", userdata)
            if sanitized == userdata:
                return {}
            try:
                logger.warning("Sanitized malformed k8s userdata JSON before parsing")
                return _loads_dict(sanitized)
            except (json.JSONDecodeError, TypeError):
                return {}
    return {}


class K8sStrategy(BaseDeploymentStrategy[K8sInfo]):
    """K8s 部署策略"""

    @staticmethod
    def _record_to_dict(record: Any) -> dict[str, Any]:
        if hasattr(record, "to_dict"):
            return record.to_dict()
        return record

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
        whl_path = kwargs.get("whl_path")
        package_name = kwargs.get("package_name")

        if not package_name and whl_path:
            from pathlib import Path
            whl_name = Path(whl_path).stem.split("-")[0].lower().replace("_", "-")
            package_name = whl_name

        k8s_defaults = settings.get_k8s_defaults()
        k8s_userdata = _parse_userdata(kwargs.get("data"))

        k8s_cof = {**k8s_defaults, **{k: v for k, v in k8s_userdata.items() if v is not None}}

        raw_data = kwargs.get("data") or {}
        raw_userdata = raw_data.get("userdata") if isinstance(raw_data, dict) else None
        record_data = k8s_cof.get("data") or {}
        if not isinstance(record_data, dict):
            record_data = {}
        if raw_userdata is not None:
            record_data["userdata"] = raw_userdata

        return {
            "deployment_id": deployment_id,
            "version": version,
            "host": kwargs.get("host", "localhost"),
            "port": kwargs.get("port"),
            "url": kwargs.get("url"),
            "whl_path": whl_path,
            "ir_path": kwargs.get("ir_path"),
            "package_name": package_name,
            "namespace": k8s_cof.get("namespace", "default"),
            "deployment_name": k8s_cof.get("deployment_name"),
            "replicas": k8s_cof.get("replicas", 1),
            "node_selector": k8s_cof.get("node_selector"),
            "deployment_type": k8s_cof.get("deployment_type"),
            "image": k8s_cof.get("image"),
            "image_pull_policy": k8s_cof.get("image_pull_policy"),
            "container_port": k8s_cof.get("container_port"),
            "service_type": k8s_cof.get("service_type"),
            "service_port": k8s_cof.get("service_port"),
            "node_port": k8s_cof.get("node_port"),
            "target_port": k8s_cof.get("target_port"),
            "created_at": now,
            "updated_at": now,
            "data": record_data if record_data else None,
        }

    def _build_deploy_context(self, record: Any, deployment: Any) -> DeployContext[K8sParams]:
        data = self._record_to_dict(record)
        record_data = data.get("data") or {}
        userdata = record_data.get("userdata") if isinstance(record_data, dict) else None

        container = K8sContainer(
            container_port=data.get("container_port"),
            image=data.get("image"),
            image_pull_policy=data.get("image_pull_policy", "IfNotPresent"),
        )

        deployment_obj = K8sDeployment(
            replicas=data.get("replicas") or 1,
            container=container,
            node_selector=data.get("node_selector"),
            deployment_type="pod",
        )

        service_port = data.get("service_port") or data.get("container_port") or data.get("port")
        service = K8sService(
            service_port=service_port,
            service_type=data.get("service_type", "LoadBalancer"),
            node_port=data.get("node_port"),
            target_port=data.get("container_port"),
        )

        k8sparams = K8sParams(
            namespace=data.get("namespace"),
            deployment_name=data.get("deployment_name"),
            config_map=record_data.get("config_map"),
            secret=record_data.get("secret"),
            whl_path=data.get("whl_path"),
            package_name=data.get("package_name"),
            deployment=deployment_obj,
            service=service,
            ir_path=data.get("ir_path"),
            userdata=userdata,
        )

        return DeployContext(
            common=CommonParams(
                deployment_id=data.get("deployment_id"),
                host=data.get("host"),
                port=data.get("port"),
                url=data.get("url"),
            ),
            params=k8sparams,
            data=data,
        )

    def _get_stop_kwargs(self, record: Any) -> dict:
        data = self._record_to_dict(record)
        return {
            "namespace": data.get("namespace"),
            "deployment_name": data.get("deployment_name"),
        }

    def _get_status_kwargs(self, record: Any) -> dict:
        data = self._record_to_dict(record)
        return {
            "namespace": data.get("namespace"),
            "deployment_name": data.get("deployment_name"),
        }
