"""Gateway ↔ Runtime 实例链路绑定编排。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.security.link_profile import (
    LinkProfile,
    LinkProfileError,
    cert_fingerprint,
)

from manager_server.infrastructure.utils import iso_datetime, utc_now
from manager_server.models.instance_models import INSTANCE_INFO_TABLE_DEF
from manager_server.models.link_binding_models import INSTANCE_LINK_BINDING_TABLE_DEF
from manager_server.schemas.link_binding_schemas import (
    LinkBindingCreateBody,
    LinkBindingView,
)

_TABLE = INSTANCE_LINK_BINDING_TABLE_DEF.table_name
_INSTANCE_TABLE = INSTANCE_INFO_TABLE_DEF.table_name
_PROTOCOL_VERSION = "0.0.1"


class LinkBindingConflict(ValueError):
    """请求与当前有效绑定冲突。"""


def _view(row: Any) -> LinkBindingView:
    data = row.data if isinstance(row.data, dict) else None
    return LinkBindingView(
        jiuwenclaw_id=row.jiuwenclaw_id,
        mtls_binding_id=row.mtls_binding_id,
        mtls_binding_epoch=int(row.mtls_binding_epoch),
        protocol_version=row.protocol_version,
        mtls_gateway_endpoint=row.mtls_gateway_endpoint,
        mtls_runtime_endpoint=row.mtls_runtime_endpoint,
        manager_cert_fingerprint=str((data or {}).get("manager_cert_fingerprint", "")),
        gateway_cert_fingerprint=row.gateway_cert_fingerprint,
        runtime_cert_fingerprint=row.runtime_cert_fingerprint,
        agentserver_cert_fingerprint=row.agentserver_cert_fingerprint,
        trust_bundle_ref=row.trust_bundle_ref,
        status=row.status,
        bound_at=iso_datetime(row.bound_at) or "",
        unbound_at=iso_datetime(getattr(row, "unbound_at", None)),
        created_at=iso_datetime(row.created_at) or "",
        updated_at=iso_datetime(row.updated_at) or "",
        updated_by=row.updated_by,
        data=data,
    )


def _same_active_binding(
    row: Any,
    body: LinkBindingCreateBody,
    *,
    gateway_endpoint: str,
    runtime_endpoint: str,
) -> bool:
    return all(
        (
            row.mtls_gateway_endpoint == gateway_endpoint,
            row.mtls_runtime_endpoint == runtime_endpoint,
            row.mtls_binding_id == body.mtls_binding_id.strip(),
            int(row.mtls_binding_epoch) == body.mtls_binding_epoch,
            str(
                (row.data if isinstance(row.data, dict) else {}).get("manager_cert_fingerprint", "")
            )
            == body.manager_cert_fingerprint,
            row.gateway_cert_fingerprint == body.gateway_cert_fingerprint.strip(),
            row.runtime_cert_fingerprint == body.runtime_cert_fingerprint.strip(),
            row.agentserver_cert_fingerprint == body.agentserver_cert_fingerprint.strip(),
            row.trust_bundle_ref == body.trust_bundle_ref.strip(),
        )
    )


def _is_integrity_error(exc: Exception) -> bool:
    return "integrity" in type(exc).__name__.lower()


class InstanceLinkBindingService:
    def __init__(self, handler: DBHandler) -> None:
        self.handler = handler

    async def get(self, jiuwenclaw_id: str) -> LinkBindingView | None:
        row = await self.handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        return _view(row) if row is not None else None

    async def bind(self, jiuwenclaw_id: str, body: LinkBindingCreateBody) -> LinkBindingView:
        instance = await self.handler.get(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if instance is None:
            raise LookupError("instance not found")

        gateway_endpoint = self._matching_instance_endpoint(
            instance,
            column="gateway_config_host",
            supplied=body.mtls_gateway_endpoint,
        )
        runtime_endpoint = self._matching_instance_endpoint(
            instance,
            column="runtime_config_host",
            supplied=body.mtls_runtime_endpoint,
        )
        current = await self.handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if current is not None and current.status == "bound":
            if _same_active_binding(
                current,
                body,
                gateway_endpoint=gateway_endpoint,
                runtime_endpoint=runtime_endpoint,
            ):
                return _view(current)
            raise LinkBindingConflict("instance already has an active link binding")

        await self._assert_endpoint_available(
            "mtls_gateway_active_key", gateway_endpoint, jiuwenclaw_id
        )
        await self._assert_endpoint_available(
            "mtls_runtime_active_key", runtime_endpoint, jiuwenclaw_id
        )

        now = utc_now()
        previous_epoch = int(getattr(current, "mtls_binding_epoch", 0) or 0)
        if current is not None and body.mtls_binding_epoch <= previous_epoch:
            raise LinkBindingConflict(
                "new binding epoch must be greater than the previous binding epoch"
            )
        data = dict(body.data or {})
        data["manager_cert_fingerprint"] = body.manager_cert_fingerprint
        payload = {
            "mtls_binding_id": body.mtls_binding_id.strip(),
            "mtls_binding_epoch": body.mtls_binding_epoch,
            "protocol_version": _PROTOCOL_VERSION,
            "mtls_gateway_endpoint": gateway_endpoint,
            "mtls_runtime_endpoint": runtime_endpoint,
            "mtls_gateway_active_key": gateway_endpoint,
            "mtls_runtime_active_key": runtime_endpoint,
            "gateway_cert_fingerprint": body.gateway_cert_fingerprint.strip(),
            "runtime_cert_fingerprint": body.runtime_cert_fingerprint.strip(),
            "agentserver_cert_fingerprint": (body.agentserver_cert_fingerprint.strip()),
            "trust_bundle_ref": body.trust_bundle_ref.strip(),
            "status": "bound",
            "bound_at": now,
            "unbound_at": None,
            "updated_at": now,
            "updated_by": body.updated_by.strip(),
            "data": data,
        }
        try:
            if current is None:
                row = await self.handler.create(
                    _TABLE,
                    {
                        "jiuwenclaw_id": jiuwenclaw_id,
                        "created_at": now,
                        **payload,
                    },
                )
            else:
                row = await self.handler.update(
                    _TABLE,
                    {
                        "jiuwenclaw_id": jiuwenclaw_id,
                        "status": current.status,
                        "mtls_binding_epoch": previous_epoch,
                    },
                    payload,
                )
        except Exception as exc:
            if _is_integrity_error(exc):
                raise LinkBindingConflict("gateway or runtime is already actively bound") from exc
            raise
        if row is None:
            raise LinkBindingConflict("link binding changed concurrently; retry")
        return _view(row)

    async def unbind(self, jiuwenclaw_id: str, *, updated_by: str) -> tuple[LinkBindingView, bool]:
        current = await self.handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if current is None:
            raise LookupError("link binding not found")
        if current.status != "bound":
            return _view(current), False

        now = utc_now()
        row = await self.handler.update(
            _TABLE,
            {
                "jiuwenclaw_id": jiuwenclaw_id,
                "status": "bound",
                "mtls_binding_epoch": int(current.mtls_binding_epoch),
            },
            {
                # 先在控制面递增 epoch 并释放唯一键；数据面必须按 API 返回的
                # rotation_required 轮换 Secret/信任包并重启后，旧身份才真正失效。
                "mtls_binding_epoch": int(current.mtls_binding_epoch) + 1,
                "mtls_gateway_active_key": None,
                "mtls_runtime_active_key": None,
                "status": "unbound",
                "unbound_at": now,
                "updated_at": now,
                "updated_by": updated_by.strip() or "system",
            },
        )
        if row is None:
            latest = await self.handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
            if latest is not None and latest.status != "bound":
                return _view(latest), False
            raise LinkBindingConflict("link binding changed concurrently; retry")
        return _view(row), True

    async def adopt_deployment_profile(
        self,
        jiuwenclaw_id: str,
        profile: LinkProfile,
    ) -> LinkBindingView:
        """Bind the co-deployed target without changing Manager's instance ID.

        This is a narrow bootstrap path for an all-in-one deployment.  It only
        adopts a profile when both instance endpoints name the exact Gateway
        and Runtime authorities declared by that profile.  Other instances use
        the explicit binding API and retain their independent IDs/materials.
        """
        instance = await self.handler.get(_INSTANCE_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if instance is None:
            raise LookupError("instance not found")
        profile_data = profile.current()
        endpoints = profile_data.get("endpoints", {})
        for role, column in (
            ("gateway", "gateway_config_host"),
            ("runtime", "runtime_config_host"),
        ):
            configured = _endpoint_authority(getattr(instance, column, ""))
            declared = _endpoint_authority(endpoints.get(role, ""))
            if not configured or configured != declared:
                raise LinkBindingConflict(
                    f"{column} does not match the installed deployment profile"
                )
        peers = profile_data.get("peers", {})
        try:
            body = LinkBindingCreateBody(
                mtls_binding_id=profile.mtls_binding_id,
                mtls_binding_epoch=profile.mtls_binding_epoch,
                mtls_gateway_endpoint=endpoints["gateway"],
                mtls_runtime_endpoint=endpoints["runtime"],
                manager_cert_fingerprint=cert_fingerprint(profile.cert_file),
                gateway_cert_fingerprint=_single_pin(peers, "gateway"),
                runtime_cert_fingerprint=_single_pin(peers, "runtime"),
                agentserver_cert_fingerprint=_single_pin(peers, "agentserver"),
                trust_bundle_ref="profile://ca",
                updated_by="deployment-profile",
                data={"binding_source": "deployment-profile"},
            )
        except (KeyError, LinkProfileError, ValueError) as exc:
            raise LinkBindingConflict("installed deployment profile is incomplete") from exc
        return await self.bind(jiuwenclaw_id, body)

    async def _assert_endpoint_available(
        self, active_key: str, endpoint: str, jiuwenclaw_id: str
    ) -> None:
        row = await self.handler.get(_TABLE, {active_key: endpoint})
        if row is not None and row.jiuwenclaw_id != jiuwenclaw_id:
            raise LinkBindingConflict(
                f"{active_key.removesuffix('_active_key')} is already actively bound"
            )

    @staticmethod
    def _matching_instance_endpoint(instance: Any, *, column: str, supplied: str) -> str:
        configured = _endpoint_authority(getattr(instance, column, ""))
        requested = _endpoint_authority(supplied)
        if not requested:
            raise LinkBindingConflict(f"invalid mTLS endpoint for {column}")
        if not configured or configured != requested:
            raise LinkBindingConflict(f"mTLS endpoint does not match instance {column}")
        return requested


def _endpoint_authority(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    try:
        valid = (
            parsed.scheme in {"", "http", "https"}
            and parsed.hostname
            and parsed.port
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return ""
    return parsed.netloc.lower() if valid else ""


def _single_pin(peers: dict, role: str) -> str:
    pins = peers.get(role)
    if not isinstance(pins, list) or len(pins) != 1:
        raise LinkProfileError(f"deployment profile requires one {role} certificate")
    return str(pins[0])
