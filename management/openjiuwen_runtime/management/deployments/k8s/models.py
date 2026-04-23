# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""K8s 部署模块数据模型"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from openjiuwen_runtime.foundation.db.table_def import TableDefinition, ColumnDefinition, IndexDefinition


@dataclass
class K8sParams:
    """K8s 部署参数"""
    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    replicas: Optional[int] = None
    config_map: Optional[dict[str, Any]] = None
    secret: Optional[dict[str, str]] = None
    image: Optional[str] = None


class K8sInfo(BaseModel):
    """K8s 部署信息模型"""
    id: int
    deployment_id: str
    version: str
    host: str
    url: Optional[str] = None
    pid: Optional[int] = None
    whl_path: Optional[str] = None
    package_name: Optional[str] = None
    template_file: Optional[str] = None
    image: Optional[str] = None
    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    replicas: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    data: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class K8sCreate(BaseModel):
    """创建 K8s 部署请求模型"""
    deployment_id: str
    version: str
    host: str
    url: Optional[str] = None
    pid: Optional[int] = None
    whl_path: Optional[str] = None
    package_name: Optional[str] = None
    template_file: Optional[str] = None
    image: Optional[str] = None
    namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    replicas: Optional[int] = None
    data: Optional[dict[str, Any]] = None


K8S_TABLE_DEF = TableDefinition(
    table_name="k8s",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("deployment_id", "string", length=64, unique=True, nullable=False),
        ColumnDefinition("version", "string", length=32, nullable=False),
        ColumnDefinition("host", "string", length=255, nullable=False),
        ColumnDefinition("url", "string", length=512, nullable=True),
        ColumnDefinition("pid", "integer", nullable=True),
        ColumnDefinition("whl_path", "string", length=512, nullable=True),
        ColumnDefinition("package_name", "string", length=255, nullable=True),
        ColumnDefinition("template_file", "string", length=512, nullable=True),
        ColumnDefinition("image", "string", length=512, nullable=True),
        ColumnDefinition("namespace", "string", length=64, nullable=True),
        ColumnDefinition("deployment_name", "string", length=255, nullable=True),
        ColumnDefinition("replicas", "integer", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
    ],
    indexes=[
        IndexDefinition(["deployment_id"], unique=True),
    ],
)
