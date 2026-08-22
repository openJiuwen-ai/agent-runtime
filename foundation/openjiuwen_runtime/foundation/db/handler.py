# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from abc import ABC, abstractmethod
from typing import Optional, Any

from .table_def import TableDefinition


class DBHandler(ABC):
    """DB操作句柄接口"""

    @abstractmethod
    async def connect(self) -> None:
        """建立数据库连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开数据库连接"""
        pass

    @abstractmethod
    async def init_table(self, table_def: TableDefinition) -> None:
        """
        初始化表（存在则跳过，不存在则创建）
        
        Args:
            table_def: 表定义
        """
        pass

    @abstractmethod
    async def create(self, table_name: str, data: dict) -> Any:
        """
        创建记录
        
        Args:
            table_name: 表名
            data: 数据字典
            
        Returns:
            创建的记录
        """
        pass

    @abstractmethod
    async def get(self, table_name: str, filters: dict) -> Optional[Any]:
        """
        读取记录
        
        Args:
            table_name: 表名
            filters: 过滤条件
            
        Returns:
            记录或None
        """
        pass

    @abstractmethod
    async def update(self, table_name: str, filters: dict, data: dict) -> Optional[Any]:
        """
        更新记录
        
        Args:
            table_name: 表名
            filters: 过滤条件
            data: 更新数据
            
        Returns:
            更新后的记录或None
        """
        pass

    @abstractmethod
    async def delete(self, table_name: str, filters: dict) -> bool:
        """
        删除记录
        
        Args:
            table_name: 表名
            filters: 过滤条件
            
        Returns:
            是否删除成功
        """
        pass

    @abstractmethod
    async def list_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """
        列出记录
        
        Args:
            table_name: 表名
            filters: 过滤条件；字段值为 list/tuple/set 时使用 SQL ``IN`` 语义
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            记录列表
        """
        pass

    @abstractmethod
    async def count_records(
        self,
        table_name: str,
        filters: Optional[dict] = None,
    ) -> int:
        """
        统计满足条件的记录条数（等价于 ``SELECT COUNT(*)`` 语义）。

        Args:
            table_name: 表名
            filters: 过滤条件；为 ``None`` 或空字典时表示全表计数；
                字段值为 list/tuple/set 时使用 SQL ``IN`` 语义

        Returns:
            记录条数
        """
        pass
