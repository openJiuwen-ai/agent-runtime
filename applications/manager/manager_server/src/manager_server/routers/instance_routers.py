"""实例管理 API（路径与设计文档 4.1 对齐）。

含 Gateway 出站：HTTP 注册 / 心跳（写入 ``instance_info.data.gateway_endpoint``）。
"""

from __future__ import annotations

from base64 import b64encode
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.security.link_auth import build_token

from manager_server.core.instance import InstanceService
from manager_server.core.instance.instance_service import (
    apply_gateway_ws_heartbeat,
    register_gateway_via_ws,
)
from manager_server.infrastructure.config import settings
from manager_server.infrastructure.db import get_db_handler
from manager_server.core.instance.pod_status_cache import (
    get_pod_status_snapshot,
)
from manager_server.schemas.common_schemas import ResponseModel
from manager_server.schemas.instance_schemas import (
    CreateInstanceBody,
    GatewayHeartbeatBody,
    GatewayRegisterBody,
    InstanceListQuery,
    InstanceUpdateBody,
)
from manager_server.security.keys import store_instance_enc_pubkey
from manager_server.security.sign_provider import get_manager_signing_key

instance_router = APIRouter()


def _svc(handler: DBHandler) -> InstanceService:
    return InstanceService(handler)


def _b64(raw: bytes) -> str:
    return b64encode(raw).decode("ascii")


def _request_volume_value(bv: dict, key: str, legacy_key: str | None = None) -> int:
    value = bv.get(key)
    if value is None and legacy_key is not None:
        value = bv.get(legacy_key)
    return int(value or 0)


def _normalize_request_volume(bv: dict) -> dict:
    return {
        "gateway_queued": _request_volume_value(bv, "gateway_queued"),
        "gateway_running": _request_volume_value(bv, "gateway_running"),
        "service_manager_queued": _request_volume_value(
            bv, "service_manager_queued", "sm_queued"
        ),
        "service_manager_routing": _request_volume_value(
            bv, "service_manager_routing", "sm_routing"
        ),
        "service_manager_running": _request_volume_value(
            bv, "service_manager_running", "sm_running"
        ),
        "requests_started_total": _request_volume_value(bv, "requests_started_total"),
        "requests_finished_total": _request_volume_value(bv, "requests_finished_total"),
        "pods_in_use": _request_volume_value(bv, "pods_in_use"),
        "pods_idle": _request_volume_value(bv, "pods_idle"),
    }


def _build_request_volume_summary(bv: dict) -> dict:
    queued_requests = _request_volume_value(
        bv, "gateway_queued"
    ) + _request_volume_value(
        bv, "service_manager_queued", "sm_queued"
    )
    running_requests = _request_volume_value(
        bv, "service_manager_running", "sm_running"
    ) or _request_volume_value(
        bv, "gateway_running"
    )
    return {
        "queued_requests": queued_requests,
        "running_requests": running_requests,
        "finished_requests": _request_volume_value(bv, "requests_finished_total"),
        "active_pods": _request_volume_value(bv, "pods_in_use"),
        "idle_pods": _request_volume_value(bv, "pods_idle"),
    }


@instance_router.post("/register", response_model=ResponseModel)
async def gateway_http_register(
    body: GatewayRegisterBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """Gateway → Manager：HTTP 注册，返回 register.ack + link_token。"""
    st = str(body.service_type or "").strip().lower()
    if st and st != "gateway":
        raise HTTPException(status_code=400, detail="only service_type=gateway accepted")
    payload = body.model_dump(exclude_none=True)
    try:
        jiuwenclaw_id = await register_gateway_via_ws(
            handler, payload, manager_id="default"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"register failed: {exc}") from exc

    enc_pubkey = str(body.enc_pubkey or "").strip()
    if enc_pubkey:
        try:
            await store_instance_enc_pubkey(
                handler,
                jiuwenclaw_id,
                enc_pubkey,
                enc_alg=str(body.enc_alg or "X25519"),
                fingerprint=str(body.enc_pubkey_fp or "") or None,
            )
        except Exception:  # noqa: BLE001
            pass

    if body.endpoint:
        await apply_gateway_ws_heartbeat(
            handler,
            jiuwenclaw_id=jiuwenclaw_id,
            manager_id="default",
            endpoint=str(body.endpoint).strip(),
            version=body.version,
        )

    data: dict[str, Any] = {
        "manager_id": "default",
        "jiuwenclaw_id": jiuwenclaw_id,
    }
    signing_key = get_manager_signing_key()
    if signing_key is not None:
        data.update(
            {
                "sign_pubkey": _b64(signing_key.public_raw),
                "sign_alg": "Ed25519",
                "key_version": signing_key.key_version,
                "sign_pubkey_fp": signing_key.fingerprint,
            }
        )
        try:
            token = build_token(
                service_id="default",
                service_type="manager",
                private_b64=_b64(signing_key.private_raw),
                public_b64=_b64(signing_key.public_raw),
            )
            if token:
                data["link_token"] = token
        except Exception:  # noqa: BLE001
            pass
    return ResponseModel(code=200, message="success", data=data)


@instance_router.post("/heartbeat", response_model=ResponseModel)
async def gateway_http_heartbeat(
    body: GatewayHeartbeatBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """Gateway → Manager：心跳 + 刷新 ``data.gateway_endpoint``。"""
    ok = await apply_gateway_ws_heartbeat(
        handler,
        jiuwenclaw_id=body.jiuwenclaw_id,
        manager_id="default",
        endpoint=(body.endpoint or "").strip() or None,
        version=body.version,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    data: dict[str, Any] = {
        "status": "ok",
        "jiuwenclaw_id": body.jiuwenclaw_id,
    }
    if body.seq is not None:
        data["seq"] = body.seq
    return ResponseModel(code=200, message="success", data=data)


@instance_router.post("/", response_model=ResponseModel)
async def create_instance(
    body: CreateInstanceBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    data = await svc.create(body)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.get("/", response_model=ResponseModel)
async def list_instances(
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    query: Annotated[InstanceListQuery, Query()],
):
    svc = _svc(handler)
    data = await svc.list_instances(query)
    return ResponseModel(code=200, message="success", data=data)


@instance_router.patch("/{jiuwenclaw_id}", response_model=ResponseModel)
async def update_instance(
    jiuwenclaw_id: str,
    body: InstanceUpdateBody,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    svc = _svc(handler)
    try:
        row = await svc.update(jiuwenclaw_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@instance_router.get("/{jiuwenclaw_id}", response_model=ResponseModel)
async def get_instance(
    jiuwenclaw_id: str, handler: Annotated[DBHandler, Depends(get_db_handler)]
):
    svc = _svc(handler)
    row = await svc.get(jiuwenclaw_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data=row.model_dump())


@instance_router.delete("/{jiuwenclaw_id}", response_model=ResponseModel)
async def delete_instance(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    force: bool = Query(False),
):
    _ = force  # 预留：后续对接 K8S 强制回收等
    svc = _svc(handler)
    ok = await svc.delete(jiuwenclaw_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return ResponseModel(code=200, message="success", data={"deleted": True})


@instance_router.get("/{jiuwenclaw_id}/pods", response_model=ResponseModel)
async def get_instance_pods(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
    include_metrics: bool = Query(False, description="是否包含 CPU/Memory 使用率"),
):
    """获取指定 jiuwenclaw 实例的所有 Pod 状态。

    Args:
        jiuwenclaw_id: jiuwenclaw 实例 ID（即 gateway_id）
        include_metrics: 是否包含 CPU/Memory 使用率

    Returns:
        {
            "code": 200,
            "message": "success",
            "data": {
                "total": 5,
                "running": 4,
                "failed": 1,
                "pods": [...]
            }
        }
    """
    # 1. 验证 jiuwenclaw_id 是否存在
    svc = _svc(handler)
    instance = await svc.get(jiuwenclaw_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")

    # 2. 读取 Gateway 上报的最近一次 Pod 状态快照（当前无写入路径时返回空）
    pod_data = get_pod_status_snapshot(jiuwenclaw_id)
    if pod_data is None:
        pod_data = {
            "source": "no_snapshot",
            "stale": True,
            "snapshot_age_seconds": None,
            "jiuwenclaw_id": jiuwenclaw_id,
            "namespace": getattr(instance, "k8s_namespace", None) or settings.k8s_namespace,
            "total": 0,
            "running": 0,
            "failed": 0,
            "pods": [],
        }
    pod_data["include_metrics_requested"] = include_metrics
    return ResponseModel(code=200, message="success", data=pod_data)


@instance_router.get("/{jiuwenclaw_id}/request-volume", response_model=ResponseModel)
async def get_instance_request_volume(
    jiuwenclaw_id: str,
    handler: Annotated[DBHandler, Depends(get_db_handler)],
):
    """获取指定 Gateway 实例的业务量统计（排队中 / 运行中消息数）。

    数据来源：Gateway 上报的 Pod 状态快照（含 request_volume 字段）。
    stale=true 表示快照已超过 90 秒未更新。
    """
    svc = _svc(handler)
    instance = await svc.get(jiuwenclaw_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")

    snapshot = get_pod_status_snapshot(jiuwenclaw_id)
    if snapshot is None:
        return ResponseModel(
            code=200,
            message="success",
            data={
                "jiuwenclaw_id": jiuwenclaw_id,
                "snapshot_time": None,
                "stale": True,
                "request_volume": None,
                "summary": None,
            },
        )

    bv = snapshot.get("request_volume")
    summary = None
    if isinstance(bv, dict):
        bv = _normalize_request_volume(bv)
        summary = _build_request_volume_summary(bv)

    return ResponseModel(
        code=200,
        message="success",
        data={
            "jiuwenclaw_id": jiuwenclaw_id,
            "snapshot_time": snapshot.get("snapshot_time"),
            "snapshot_age_seconds": snapshot.get("snapshot_age_seconds"),
            "stale": snapshot.get("stale", True),
            "request_volume": bv,
            "summary": summary,
        },
    )
