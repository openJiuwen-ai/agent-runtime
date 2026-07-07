# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class SchemaCheckResult:
    ok: bool
    missing: list[str]
    current_version: str | None = None


@dataclass
class PreflightResult:
    ok: bool
    details: dict[str, Any]


class SchemaManager:
    def __init__(
        self,
        *,
        required_tables: Iterable[str],
        required_indexes: Iterable[str],
        schema_version_getter,
        table_exists_checker,
        index_exists_checker,
    ) -> None:
        self._required_tables = list(required_tables)
        self._required_indexes = list(required_indexes)
        self._schema_version_getter = schema_version_getter
        self._table_exists_checker = table_exists_checker
        self._index_exists_checker = index_exists_checker

    async def check_required_tables(self) -> SchemaCheckResult:
        missing: list[str] = []
        for table in self._required_tables:
            exists = await self._table_exists_checker(table)
            if not exists:
                missing.append(table)
        return SchemaCheckResult(ok=not missing, missing=missing)

    async def check_required_indexes(self) -> SchemaCheckResult:
        missing: list[str] = []
        for idx in self._required_indexes:
            exists = await self._index_exists_checker(idx)
            if not exists:
                missing.append(idx)
        return SchemaCheckResult(ok=not missing, missing=missing)

    async def check_schema_version(self, min_version: str) -> SchemaCheckResult:
        current = await self._schema_version_getter()
        ok = str(current or "") >= str(min_version)
        return SchemaCheckResult(ok=ok, missing=[], current_version=current)

    async def preflight(self, *, min_version: str = "0") -> PreflightResult:
        table_result = await self.check_required_tables()
        index_result = await self.check_required_indexes()
        version_result = await self.check_schema_version(min_version)
        ok = table_result.ok and index_result.ok and version_result.ok
        return PreflightResult(
            ok=ok,
            details={
                "tables": table_result,
                "indexes": index_result,
                "version": version_result,
            },
        )
