from __future__ import annotations

import hashlib
import re
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openjiuwen_runtime.foundation.db.handler import DBHandler

from manager_server.core.import_export import ImportExportContext, adapter_registry

# Import registers the built-in cluster adapter.  Future domains only need to
# implement and register another adapter; the routes remain unchanged.
from manager_server.core.import_export import cluster as _cluster_adapter  # noqa: F401
from manager_server.core.import_export.workbook import dump_workbook, load_workbook_data
from manager_server.infrastructure.db import get_db_handler
from manager_server.routers.deps import AdminUser
from manager_server.schemas.common_schemas import ResponseModel

import_export_router = APIRouter(prefix="/import-export")
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _context(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    authorization: Annotated[str | None, Header()] = None,
) -> ImportExportContext:
    return ImportExportContext(handler=handler, authorization=authorization)


_ImportExportRequestContext = Annotated[ImportExportContext, Depends(_context)]


def _adapter(resource_type: str):
    try:
        return adapter_registry.get(resource_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@import_export_router.get("/formats", response_model=ResponseModel)
async def list_import_export_formats(admin: AdminUser):
    _ = admin
    return ResponseModel(
        code=200, message="success", data={"resource_types": adapter_registry.formats()}
    )


@import_export_router.get("/{resource_type}/{resource_id}/export")
async def export_resource(
    resource_type: str,
    resource_id: str,
    context: _ImportExportRequestContext,
    admin: AdminUser,
):
    _ = admin
    try:
        workbook = await _adapter(resource_type).export(context, resource_id)
        payload = dump_workbook(workbook)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = _SAFE_FILENAME.sub("_", workbook.resource_name).strip("._") or resource_type
    filename = f"{name}-{workbook.resource_id}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@import_export_router.post("/{resource_type}/preflight", response_model=ResponseModel)
async def preflight_import(
    resource_type: str,
    request: Request,
    context: _ImportExportRequestContext,
    admin: AdminUser,
):
    _ = admin
    raw = await request.body()
    try:
        workbook = load_workbook_data(raw, expected_resource_type=resource_type)
        report = await _adapter(resource_type).preflight(context, workbook)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report["confirmation_token"] = hashlib.sha256(raw).hexdigest()
    return ResponseModel(code=200, message="success", data=report)


@import_export_router.post("/{resource_type}/import", response_model=ResponseModel)
async def import_resource(
    resource_type: str,
    request: Request,
    context: _ImportExportRequestContext,
    admin: AdminUser,
    confirmation_token: str = Query(..., min_length=64, max_length=64),
):
    _ = admin
    raw = await request.body()
    if not hashlib.sha256(raw).hexdigest() == confirmation_token:
        raise HTTPException(status_code=409, detail="workbook changed after preflight")
    try:
        workbook = load_workbook_data(raw, expected_resource_type=resource_type)
        result = await _adapter(resource_type).apply(context, workbook)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseModel(code=200, message="success", data=result)
