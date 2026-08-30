# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""数据库公用工具集。

现阶段提供 db 类型判断谓词（``is_sqlite`` / ``is_mysql`` / ``is_postgresql`` / ``is_gaussdb``），
统一各处 ``db_type == "sqlite"`` / ``in {"postgresql", "postgres", "pg"}`` 等分散写法，
避免别名集合散落、不一致（有的用 set、有的用 tuple、有的漏了 alias）。
后续其它数据库公用函数也归集于此。
"""

from __future__ import annotations

_SQLITE = frozenset({"sqlite"})
_MYSQL = frozenset({"mysql", "mariadb"})
_POSTGRESQL = frozenset({"postgresql", "postgres", "pg"})
_GAUSSDB = frozenset({"gaussdb", "opengauss"})


def is_sqlite(db_type: str) -> bool:
    return (db_type or "").strip().lower() in _SQLITE


def is_mysql(db_type: str) -> bool:
    return (db_type or "").strip().lower() in _MYSQL


def is_postgresql(db_type: str) -> bool:
    return (db_type or "").strip().lower() in _POSTGRESQL


def is_gaussdb(db_type: str) -> bool:
    return (db_type or "").strip().lower() in _GAUSSDB
