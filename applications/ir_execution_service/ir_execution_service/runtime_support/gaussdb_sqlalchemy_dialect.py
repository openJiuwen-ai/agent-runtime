# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""IR 内部兼容别名：实际实现位于 foundation.db.dialects.gaussdb_asyncgaussdb。

保留本模块是为了 IR 作为独立部署单元时的包内 import 路径稳定
（`from .gaussdb_sqlalchemy_dialect import ensure_gaussdb_dialect_registered`），
同时避免与 foundation 维护两份几乎相同的方言实现造成代码漂移。
"""
from __future__ import annotations

from openjiuwen_runtime.foundation.db.dialects.gaussdb_asyncgaussdb import (
    AsyncAdapt_async_gaussdb_dbapi,
    PGDialect_async_gaussdb,
    dialect,
    ensure_async_gaussdb_installed,
    ensure_gaussdb_dialect_registered,
)

__all__ = [
    "AsyncAdapt_async_gaussdb_dbapi",
    "PGDialect_async_gaussdb",
    "dialect",
    "ensure_async_gaussdb_installed",
    "ensure_gaussdb_dialect_registered",
]