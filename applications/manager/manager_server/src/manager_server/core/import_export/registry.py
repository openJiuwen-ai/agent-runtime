from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openjiuwen_runtime.foundation.db.handler import DBHandler


@dataclass(slots=True)
class SheetData:
    name: str
    headers: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    sensitive_cells: set[tuple[int, str]] = field(default_factory=set)


@dataclass(slots=True)
class WorkbookData:
    resource_type: str
    resource_id: str
    resource_name: str
    sheets: list[SheetData]
    format_version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImportExportContext:
    handler: DBHandler
    authorization: str | None = None


class ImportExportAdapter(Protocol):
    resource_type: str

    async def export(self, context: ImportExportContext, resource_id: str) -> WorkbookData:
        ...

    async def preflight(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        ...

    async def apply(
        self, context: ImportExportContext, workbook: WorkbookData
    ) -> dict[str, Any]:
        ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ImportExportAdapter] = {}

    def register(self, adapter: ImportExportAdapter) -> None:
        key = str(adapter.resource_type or "").strip().lower()
        if not key:
            raise ValueError("resource_type is required")
        if key in self._adapters:
            raise ValueError(f"import/export adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, resource_type: str) -> ImportExportAdapter:
        key = str(resource_type or "").strip().lower()
        adapter = self._adapters.get(key)
        if adapter is None:
            raise KeyError(f"unsupported import/export resource type: {key}")
        return adapter

    def formats(self) -> list[str]:
        return sorted(self._adapters)


adapter_registry = AdapterRegistry()
