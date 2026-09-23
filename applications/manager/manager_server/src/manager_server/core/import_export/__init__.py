"""Reusable Manager import/export framework.

Business domains register an adapter.  The HTTP layer and workbook transport do
not know anything about clusters, templates, or other future resource types.
"""

from .registry import ImportExportContext, adapter_registry

__all__ = ("ImportExportContext", "adapter_registry")
