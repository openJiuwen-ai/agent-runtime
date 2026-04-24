"""K8s 部署模块数据模型"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, List

from pydantic import BaseModel, Field

from openjiuwen_runtime.foundation.db.table_def import TableDefinition, ColumnDefinition, IndexDefinition


class K8sContainer(BaseModel):
    image: Optional[str] = Field(None, description="镜像")
    image_pull_policy: Optional[str] = Field("IfNotPresent", description="镜像下载规则")
    container_port: Optional[int] = None


class K8sDeployment(BaseModel):
    replicas: Optional[int] = Field(1, description="副本数")
    container: Optional[K8sContainer] = None
    node_selector: Optional[dict] = None
    deployment_type: Optional[str] = Field("pod", description="部署类型")


class K8sService(BaseModel):
    service_type: Optional[str] = Field("LoadBalancer", description="服务规则")
    node_port: Optional[int] = None
    target_port: Optional[int] = None
    service_port: Optional[int] = None


@dataclass
class K8sParams:
    """K8s 部署参数"""
    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    config_map: Optional[dict[str, Any]] = None
    secret: Optional[dict[str, str]] = None
    whl_path: Optional[str] = None
    package_name: Optional[str] = None
    deployment: Optional[K8sDeployment] = field(default=None)
    service: Optional[K8sService] = field(default=None)
    ir_path: Optional[str] = None
    userdata: Optional[str] = None


class K8sInfo(BaseModel):
    """K8s 部署信息模型"""
    id: int
    deployment_id: str
    version: str
    host: str
    port: Optional[int] = None
    url: Optional[str] = None
    whl_path: Optional[str] = None
    ir_path: Optional[str] = None
    package_name: Optional[str] = None
    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    replicas: Optional[int] = None
    image: Optional[str] = None
    container_port: Optional[int] = None
    node_port: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    data: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class K8sCreate(BaseModel):
    """创建 K8s 部署请求模型"""
    deployment_id: str = Field(..., description="部署ID")
    version: str = Field(..., description="版本号")
    host: str = Field(..., description="主机地址")
    port: Optional[int] = Field(None, description="端口")
    url: Optional[str] = Field(None, description="服务URL")
    whl_path: Optional[str] = Field(None, description="WHL包路径")
    ir_path: Optional[str] = Field(None, description="IR文件路径")
    package_name: Optional[str] = Field(None, description="包名称")
    namespace: Optional[str] = Field(None, description="命名空间")
    deployment_name: Optional[str] = Field(None, description="部署名称")
    replicas: Optional[int] = Field(1, description="副本数")
    node_selector: Optional[dict[str, Any]] = Field(None, description="选择部署节点")
    deployment_type: Optional[str] = Field(None, description="部署类型")
    image: Optional[str] = Field(None, description="镜像")
    image_pull_policy: Optional[str] = Field(None, description="镜像拉取规则")
    container_port: Optional[int] = Field(None, description="容器端口")
    service_type: Optional[str] = Field(None, description="服务类型")
    service_port: Optional[int] = Field(None, description="服务端口")
    node_port: Optional[int] = Field(None, description="对外访问端口")
    target_port: Optional[int] = Field(None, description="目标端口")
    data: Optional[dict[str, Any]] = Field(None, description="扩展数据")

K8S_TABLE_DEF = TableDefinition(
    table_name="k8s",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("deployment_id", "string", length=64, unique=True, nullable=False),
        ColumnDefinition("version", "string", length=32, nullable=False),
        ColumnDefinition("host", "string", length=255, nullable=False),
        ColumnDefinition("port", "integer", nullable=True),
        ColumnDefinition("url", "string", length=512, nullable=True),
        ColumnDefinition("whl_path", "string", length=512, nullable=True),
        ColumnDefinition("ir_path", "string", length=512, nullable=True),
        ColumnDefinition("package_name", "string", length=255, nullable=True),
        ColumnDefinition("namespace", "string", length=128, nullable=True),
        ColumnDefinition("deployment_name", "string", length=255, nullable=True),
        ColumnDefinition("replicas", "integer", nullable=True),
        ColumnDefinition("node_selector", "json", nullable=True),
        ColumnDefinition("deployment_type", "string", length=255, nullable=True),
        ColumnDefinition("image", "string", length=512, nullable=True),
        ColumnDefinition("image_pull_policy", "string", length=512, nullable=True),
        ColumnDefinition("container_port", "integer", nullable=True),
        ColumnDefinition("service_type", "string", length=512, nullable=True),
        ColumnDefinition("service_port", "integer", nullable=True),
        ColumnDefinition("node_port", "integer", nullable=True),
        ColumnDefinition("target_port", "integer", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
    ],
    indexes=[
        IndexDefinition(["deployment_id"], unique=True),
        IndexDefinition(["namespace", "deployment_name"], unique=False),
    ],
)
