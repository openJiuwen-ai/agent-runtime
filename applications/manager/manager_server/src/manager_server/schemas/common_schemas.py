from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

# MySQL / SQLAlchemy ``integer`` 列（有符号 INT32）可表示范围。
INT32_MIN = -2_147_483_648
INT32_MAX = 2_147_483_647

__all__ = ("ResponseModel", "INT32_MIN", "INT32_MAX")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: T | None = None
