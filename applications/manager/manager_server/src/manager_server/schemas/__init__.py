"""Pydantic schema 子模块；请从具体子模块导入，避免包初始化时拉取全部 schema。"""

from manager_server.schemas.common_schemas import ResponseModel

__all__ = ("ResponseModel",)
