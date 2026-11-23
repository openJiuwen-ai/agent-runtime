"""Import deployment-rendered runtime definitions without modifying existing bindings.

Only a server-configured file is read. Content-addressed IDs make retries and
multi-worker startup idempotent, and isolate changed deployment revisions from
already-bound templates. No file contents are returned by the status endpoint.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from openjiuwen_runtime.foundation.db.handler import DBHandler
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from manager_server.core.template.service_config_container import ServiceConfigContainerService, wire_to_row
from manager_server.core.template.service_config_template import ServiceConfigTemplateService
from manager_server.infrastructure.logger import get_logger
from manager_server.infrastructure.utils import utc_now
from manager_server.schemas.template_schemas import (
    ServiceConfigContainerCreateBody,
    ServiceConfigTemplateCreateBody,
    ServiceConfigTemplateOut,
    DeploymentDefaultsRestoreBody,
    ContainerDeploymentDefaultsRestoreBody,
    ServiceConfigContainerOut,
)

_log = get_logger(__name__)
_MAX_BYTES = 1_000_000
_CONTAINERS = "service_config_container"
_TEMPLATES = "service_config_template"
_PLACEHOLDER = re.compile(r"<<[A-Z][A-Z0-9_]*>>|\$\{[A-Z][A-Z0-9_]*\}")
_CONTAINER_FIELDS = {
    "container_id", "name", "image", "data", "ports", "env", "resources", "command", "args",
    "imagePullPolicy", "image_pull_policy", "envFrom", "env_from", "volumeMounts", "volume_mounts",
    "securityContext", "security_context", "readinessProbe", "readiness_probe",
}
_CONTAINER_ALIASES = {
    "imagePullPolicy": "image_pull_policy", "envFrom": "env_from",
    "volumeMounts": "volume_mounts", "securityContext": "security_context",
    "readinessProbe": "readiness_probe",
}


def _validate_fields(wire: dict[str, Any], allowed: set[str], aliases: dict[str, str]) -> None:
    if set(wire) - allowed:
        raise ValueError("deployment configuration contains unsupported fields; update Manager and Runtime together")
    if any(left in wire and right in wire for left, right in aliases.items()):
        raise ValueError("deployment configuration contains ambiguous field aliases")
    if wire.get("data") is not None and not isinstance(wire["data"], dict):
        raise ValueError("data must be an object")


@dataclass
class DeploymentPlan:
    source: str
    fingerprint: str
    containers: list[dict[str, Any]]
    templates: list[dict[str, Any]]


def _status(status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "message": message, "templates": [], "container_count": 0, **extra}


def _configured_paths() -> list[Path]:
    explicit = os.getenv("AGENTSERVER_JSON_PATH", "").strip()
    tool_dir = os.getenv("MANAGER_DEPLOY_TOOL_DIR", "").strip()
    # An explicit mount is authoritative; never silently fall back to an older file.
    if explicit:
        return [Path(explicit)]
    return [Path(tool_dir) / "conf" / "agentserver.json"] if tool_dir else []


def _rows(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{name} must be a nonempty object array")
    return value


def _unique_ids(rows: list[dict[str, Any]], key: str) -> list[str]:
    ids = [row.get(key) for row in rows]
    if any(not isinstance(item, str) or not item.strip() for item in ids):
        raise ValueError(f"{key} must be a nonempty string")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate {key}")
    return ids


def build_deployment_plan(document: Any, source: str) -> DeploymentPlan:
    if not isinstance(document, dict):
        raise ValueError("agentserver.json must contain a JSON object")
    if "rawdata" in document and document.get("type") != "config_sync":
        raise ValueError("agentserver.json envelope type must be config_sync")
    raw = document.get("rawdata", document)
    if not isinstance(raw, dict):
        raise ValueError("rawdata must be an object")
    if set(raw) - {"containers", "templates", "scopes"}:
        raise ValueError("unsupported config_sync section")
    containers = _rows(raw.get("containers"), "containers")
    templates = _rows(raw.get("templates"), "templates")
    container_ids = _unique_ids(containers, "container_id")
    _unique_ids(templates, "template_id")
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if _PLACEHOLDER.search(canonical):
        raise ValueError("agentserver.json contains unresolved deployment variables")
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    mapped = {cid: f"deploy-{uuid5(NAMESPACE_URL, fingerprint + ':container:' + cid).hex}" for cid in container_ids}
    now = utc_now()
    container_rows: list[dict[str, Any]] = []
    for wire in containers:
        _validate_fields(wire, _CONTAINER_FIELDS, _CONTAINER_ALIASES)
        if not isinstance(wire.get("image"), str) or not wire["image"].strip():
            raise ValueError("container image is required")
        row = wire_to_row(wire)
        row["container_id"] = mapped[wire["container_id"]]
        # Validate column lengths and types before writing any row.
        ServiceConfigContainerCreateBody(template_name=wire.get("name") or "agent", **row)
        data = dict(row.get("data") or {})
        data["_meta"] = {
            "template_name": f"部署默认 · {row['name']} · {fingerprint[:8]}"[:128],
            "description": "由部署工具渲染的 agentserver.json 自动生成",
            "enabled": True,
        }
        data["deployment_default"] = {"fingerprint": fingerprint, "source_container_id": wire["container_id"]}
        row.update(data=data, created_at=now, updated_at=now)
        container_rows.append(row)
    template_rows: list[dict[str, Any]] = []
    for wire in templates:
        # Runtime does not yet implement runtimeClassName. Do not silently drop
        # sandbox isolation requested by a deployment and call it usable.
        if wire.get("runtimeClassName"):
            raise ValueError("runtimeClassName is not supported by the current Runtime; cannot import this deployment safely")
        _validate_fields(
            wire, set(ServiceConfigTemplateCreateBody.model_fields) | {"template_id", "nodeName", "fsGroup"},
            {"nodeName": "node_name", "fsGroup": "fs_group"},
        )
        main = wire.get("main_container_id")
        sidecars = wire.get("sidecar_container_ids", [])
        if not isinstance(main, str) or main not in mapped:
            raise ValueError("main_container_id references a missing container")
        if not isinstance(sidecars, list) or any(not isinstance(cid, str) or cid not in mapped for cid in sidecars):
            raise ValueError("sidecar_container_ids references a missing container")
        if len(set([main, *sidecars])) != len([main, *sidecars]):
            raise ValueError("a runtime template cannot bind the same container twice")
        values = {key: value for key, value in wire.items() if key in ServiceConfigTemplateCreateBody.model_fields}
        values.update(
            template_name=f"部署默认 · {wire.get('template_name') or wire['template_id']} · {fingerprint[:8]}"[:128],
            description=wire.get("description") or "由部署工具渲染的 agentserver.json 自动生成；绑定到集群后使用",
            main_container_id=mapped[main],
            sidecar_container_ids=[mapped[cid] for cid in sidecars],
            node_name=wire.get("nodeName", wire.get("node_name")),
            fs_group=wire.get("fsGroup", wire.get("fs_group")),
        )
        data = dict(wire.get("data") or {})
        data["deployment_default"] = {"fingerprint": fingerprint, "source_template_id": wire["template_id"], "source": source}
        data["config_sync"] = {"source_template_id": wire["template_id"]}
        values["data"] = data
        body = ServiceConfigTemplateCreateBody(**values)
        tid = str(uuid5(NAMESPACE_URL, fingerprint + ':template:' + wire["template_id"]))
        row = ServiceConfigTemplateService._build_row_for_create(body, template_id=tid, data=data)
        row.update(created_at=now, updated_at=now)
        template_rows.append(row)
    return DeploymentPlan(source, fingerprint, container_rows, template_rows)


def read_deployment_plan() -> DeploymentPlan | dict[str, Any]:
    paths = _configured_paths()
    if not paths:
        return _status("unconfigured", "Manager 未接入部署生成的 agentserver.json。请更新部署工具并重新部署 Manager。")
    path = paths[0]
    try:
        with path.open("rb") as stream:
            payload = stream.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise ValueError("agentserver.json exceeds the 1 MB limit")
        return build_deployment_plan(json.loads(payload), str(path))
    except FileNotFoundError:
        return _status("not_found", "未找到渲染配置。请先部署 Runtime，再重新部署 Manager。", source=str(path))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _status("invalid", "无法读取 agentserver.json，请检查文件权限与 JSON 格式。", source=str(path))
    except ValidationError as exc:
        # ValidationError text includes input values, which may contain secrets.
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors(include_input=False))
        return _status("invalid", f"部署配置字段校验失败：{fields}", source=str(path))
    except (ValueError, TypeError) as exc:
        return _status("invalid", str(exc), source=str(path))


async def _inspect(handler: DBHandler, plan: DeploymentPlan) -> dict[str, Any]:
    existing_containers = {}
    for expected in plan.containers:
        cid = expected["container_id"]
        existing_containers[cid] = await handler.get(_CONTAINERS, {"container_id": cid})
    summaries = []
    for expected in plan.templates:
        row = await handler.get(_TEMPLATES, {"template_id": expected["template_id"]})
        if row is None:
            continue
        ids = [row.main_container_id, *(row.sidecar_container_ids or [])]
        valid = True
        for cid in ids:
            container = existing_containers.get(cid) or await handler.get(_CONTAINERS, {"container_id": cid})
            if container is None or not container.image or not (container.data or {}).get("_meta", {}).get("enabled", True):
                valid = False
        customized = any(
            getattr(row, key, None) != value
            for key, value in expected.items() if key not in {"created_at", "updated_at"}
        ) or any(
            existing_containers.get(expected_container["container_id"]) is not None and any(
                getattr(existing_containers[expected_container["container_id"]], key, None) != value
                for key, value in expected_container.items() if key not in {"created_at", "updated_at"}
            )
            for expected_container in plan.containers
            if expected_container["container_id"] in ids
        )
        summaries.append({"template_id": row.template_id, "template_name": row.template_name, "enabled": bool(row.enabled and valid), "customized": customized})
    complete = len(summaries) == len(plan.templates) and all(existing_containers.values())
    return _status(
        "ready" if complete else "available",
        "部署默认模板已生成；仍需在集群中绑定并验证。" if complete else "配置已发现，等待生成配套模板。",
        source=plan.source, fingerprint=plan.fingerprint, templates=summaries,
        container_count=len(plan.containers),
        namespaces=sorted({row["namespace"] for row in plan.templates}),
    )


async def deployment_defaults_status(handler: DBHandler) -> dict[str, Any]:
    plan = read_deployment_plan()
    return plan if isinstance(plan, dict) else await _inspect(handler, plan)


class DeploymentDefaultsChanged(ValueError):
    """The deployment source changed after the user loaded the form."""


class DefaultContainerConflict(ValueError):
    """The requested copy ID is already in use; existing containers are untouched."""


def _container_body(row: dict[str, Any]) -> ServiceConfigContainerCreateBody:
    values = {
        key: deepcopy(value) for key, value in row.items()
        if key in ServiceConfigContainerCreateBody.model_fields
    }
    values.update(deepcopy(row["data"]["_meta"]))
    return ServiceConfigContainerCreateBody(**values)


def preview_default_containers() -> dict[str, Any]:
    """Expose editable deployment containers to administrators without DB writes."""
    plan = read_deployment_plan()
    if isinstance(plan, dict):
        return plan
    templates = []
    for row in plan.containers:
        body = _container_body(row)
        # A fresh copy ID is editable in the form. It never names the bootstrap row.
        body.container_id = f"restore-{uuid4().hex}"
        templates.append({
            "source_container_id": row["data"]["deployment_default"]["source_container_id"],
            "template": body.model_dump(mode="json"),
        })
    return _status(
        "available", "默认容器配置已读取，检查后保存为新的容器模板。",
        source=plan.source, fingerprint=plan.fingerprint, templates=templates,
        container_count=len(templates),
    )


async def restore_default_container(
    handler: DBHandler, request: ContainerDeploymentDefaultsRestoreBody,
) -> ServiceConfigContainerOut:
    """Create one independent container copy; never mutate templates or bindings."""
    plan = read_deployment_plan()
    if isinstance(plan, dict):
        raise ValueError(plan["message"])
    if request.fingerprint != plan.fingerprint:
        raise DeploymentDefaultsChanged("部署默认配置已更新，请重新读取默认模板后保存。")
    source = next((row for row in plan.containers if
        row["data"]["deployment_default"]["source_container_id"] == request.source_container_id), None)
    if source is None:
        raise ValueError("所选默认容器不存在，请重新读取默认模板。")
    values = _container_body(source).model_dump()
    values.update(request.template.model_dump(exclude_unset=True))
    data = deepcopy(source.get("data") or {})
    data.update(deepcopy(request.template.data or {}))
    data["deployment_default"] = deepcopy(source["data"]["deployment_default"])
    sync = data.get("config_sync")
    if isinstance(sync, dict):
        data["config_sync"] = {key: value for key, value in sync.items() if key not in {"containers", "scopes"}}
    values["data"] = data
    body = ServiceConfigContainerCreateBody(**values)
    row = wire_to_row(body.model_dump())
    row["data"]["_meta"] = {
        "template_name": body.template_name, "description": body.description, "enabled": body.enabled,
    }
    now = utc_now()
    row.update(created_at=now, updated_at=now)
    try:
        async with handler.session_factory() as session:
            async with session.begin():
                table = handler.get_table(_CONTAINERS)
                existing = await session.execute(select(table.c.container_id).where(table.c.container_id == body.container_id))
                if existing.first() is not None:
                    raise DefaultContainerConflict("容器 ID 已存在，请修改后保存；已有容器配置不会被覆盖。")
                await session.execute(table.insert().values(**row))
    except IntegrityError as exc:
        raise DefaultContainerConflict("容器 ID 已存在，请修改后保存；已有容器配置不会被覆盖。") from exc
    saved = await ServiceConfigContainerService(handler).get(body.container_id)
    assert saved is not None
    return saved


def _preview_container_id(container_id: str) -> str:
    # These IDs only identify unsaved form choices, never catalog records.
    return f"default-preview-{uuid5(NAMESPACE_URL, container_id).hex}"


def _template_body(row: dict[str, Any]) -> ServiceConfigTemplateCreateBody:
    return ServiceConfigTemplateCreateBody(**{
        key: deepcopy(value) for key, value in row.items()
        if key in ServiceConfigTemplateCreateBody.model_fields
    })


def preview_deployment_defaults() -> dict[str, Any]:
    """Read the original deployment source without creating or repairing DB rows."""
    plan = read_deployment_plan()
    if isinstance(plan, dict):
        return plan
    by_id = {row["container_id"]: row for row in plan.containers}
    templates = []
    for row in plan.templates:
        body = _template_body(row)
        ids = [body.main_container_id, *(body.sidecar_container_ids or [])]
        containers = []
        for cid in ids:
            container = by_id[cid]
            draft_id = _preview_container_id(cid)
            containers.append({
                "container_id": draft_id, "template_id": draft_id,
                "template_name": container["data"]["_meta"]["template_name"],
                "image": container["image"], "enabled": True,
            })
        body.main_container_id = _preview_container_id(body.main_container_id)
        body.sidecar_container_ids = [_preview_container_id(cid) for cid in body.sidecar_container_ids or []]
        templates.append({
            "source_template_id": row["data"]["deployment_default"]["source_template_id"],
            "template": body.model_dump(mode="json"), "containers": containers,
        })
    return _status(
        "available", "默认配置已读取，保存后创建新的运行时模板与所需容器模板。",
        source=plan.source, fingerprint=plan.fingerprint, templates=templates,
        container_count=len(plan.containers),
    )


async def restore_deployment_defaults(
    handler: DBHandler, request: DeploymentDefaultsRestoreBody,
) -> ServiceConfigTemplateOut:
    """Save a form loaded from deployment defaults, without overwriting any record.

    Default containers are read again on the server and copied to fresh IDs in the
    same transaction as the runtime template. A damaged or deleted catalog entry
    therefore cannot affect recovery, and cancelling a preview leaves no records.
    """
    plan = read_deployment_plan()
    if isinstance(plan, dict):
        raise ValueError(plan["message"])
    if request.fingerprint != plan.fingerprint:
        raise DeploymentDefaultsChanged("部署默认配置已更新，请重新读取默认模板后保存。")
    source = next((row for row in plan.templates if
        row["data"]["deployment_default"]["source_template_id"] == request.source_template_id), None)
    if source is None:
        raise ValueError("所选默认模板不存在，请重新读取默认模板。")

    values = _template_body(source).model_dump()
    # Omitted bindings still mean fresh source copies, never possibly modified
    # or deleted deterministic catalog rows from the initial bootstrap.
    values["main_container_id"] = _preview_container_id(source["main_container_id"])
    values["sidecar_container_ids"] = [
        _preview_container_id(cid) for cid in source.get("sidecar_container_ids") or []
    ]
    values.update(request.template.model_dump(exclude_unset=True))
    # Keep source metadata while accepting non-reserved form metadata. The
    # recovery path never accepts inline container writes or deployment grants.
    data = deepcopy(source.get("data") or {})
    data.update(deepcopy(request.template.data or {}))
    data["deployment_default"] = deepcopy(source["data"]["deployment_default"])
    sync = data.get("config_sync")
    data["config_sync"] = {
        **({key: value for key, value in sync.items() if key not in {"containers", "scopes"}}
           if isinstance(sync, dict) else {}),
        "source_template_id": request.source_template_id,
    }
    values["data"] = data
    body = ServiceConfigTemplateCreateBody(**values)
    main = (body.main_container_id or "").strip()
    sidecars = body.sidecar_container_ids or []
    if not main or any(not cid.strip() for cid in sidecars):
        raise ValueError("请选择主容器，并补全 Sidecar 容器选择。")
    ids = [main, *sidecars]
    if len(ids) != len(set(ids)):
        raise ValueError("主容器和 Sidecar 不能重复选择同一个容器模板。")

    source_ids = {source["main_container_id"], *(source.get("sidecar_container_ids") or [])}
    draft_containers = {
        _preview_container_id(row["container_id"]): row
        for row in plan.containers if row["container_id"] in source_ids
    }
    mapped = {cid: f"restore-{uuid4().hex}" for cid in ids if cid in draft_containers}
    now = utc_now()
    template_id = str(uuid4())
    async with handler.session_factory() as session:
        async with session.begin():
            containers = handler.get_table(_CONTAINERS)
            for cid in ids:
                if cid in mapped:
                    row = deepcopy(draft_containers[cid])
                    row.update(container_id=mapped[cid], created_at=now, updated_at=now)
                    await session.execute(containers.insert().values(**row))
                else:
                    if cid.startswith("default-preview-"):
                        raise ValueError("默认容器选择已失效，请重新读取默认模板。")
                    existing = await session.execute(select(containers.c.container_id).where(containers.c.container_id == cid))
                    if existing.first() is None:
                        raise ValueError("所选容器模板已不存在，请重新选择。")
            body.main_container_id = mapped.get(main, main)
            body.sidecar_container_ids = [mapped.get(cid, cid) for cid in sidecars]
            row = ServiceConfigTemplateService._build_row_for_create(body, template_id=template_id, data=data)
            row.update(created_at=now, updated_at=now)
            await session.execute(handler.get_table(_TEMPLATES).insert().values(**row))
    saved = await ServiceConfigTemplateService(handler).get(template_id)
    assert saved is not None
    return saved


async def _insert_plan(handler: DBHandler, plan: DeploymentPlan) -> int:
    # All shipped Manager DB handlers share SQLAlchemy's transaction interface.
    # A failure must not leave orphan containers or half a deployment snapshot.
    for attempt in range(3):
        try:
            created = 0
            async with handler.session_factory() as session:
                async with session.begin():
                    for name, key, rows in (
                        (_CONTAINERS, "container_id", plan.containers),
                        (_TEMPLATES, "template_id", plan.templates),
                    ):
                        table = handler.get_table(name)
                        for row in rows:
                            existing = await session.execute(select(table.c[key]).where(table.c[key] == row[key]))
                            if existing.first() is not None:
                                continue
                            await session.execute(table.insert().values(**row))
                            if name == _TEMPLATES:
                                created += 1
            return created
        except IntegrityError:
            # A concurrent worker may commit the same deterministic IDs first.
            # Restart the entire transaction and inspect its committed rows.
            if attempt == 2:
                raise
    return 0


async def initialize_deployment_defaults(handler: DBHandler) -> dict[str, Any]:
    plan = read_deployment_plan()
    if isinstance(plan, dict):
        return plan
    created = await _insert_plan(handler, plan)
    return {**await _inspect(handler, plan), "created": created}


async def bootstrap_deployment_defaults(handler: DBHandler) -> None:
    if os.getenv("MANAGER_RUNTIME_DEFAULTS_ENABLED", "true").lower() in {"false", "0", "no"}:
        return
    try:
        result = await initialize_deployment_defaults(handler)
        if result["status"] == "unconfigured":
            return
        _log.info("deployment runtime defaults", status=result["status"], created=result.get("created", 0))
    except Exception as exc:
        # A bad optional deployment seed must not make the management UI crash.
        # Operators can retry through the authenticated POST endpoint.
        _log.warning("deployment runtime defaults initialization failed", error_type=type(exc).__name__)
