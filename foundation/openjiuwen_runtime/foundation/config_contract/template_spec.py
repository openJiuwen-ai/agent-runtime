# coding: utf-8
"""三段式模板契约:模板级字段校验 + 容器/卷水合(Manager/Runtime 共用)。

Runtime 的 ``config_sync`` 与 Manager 的保存前预检都走这里,避免两套规则
(issue #203:Manager 允许保存的配置被 Runtime 整包 400)。

本模块只做「校验 + 水合」,不构造 Runtime 的 ``Template`` 业务对象——缺省
字段由调用方构造 ``Template`` 时按 dataclass 默认值补齐(与历史行为一致)。
"""

from __future__ import annotations

import re
from typing import Any

from .container_spec import (
    build_canonical,
    canonical_volumes,
    mounted_volume_names,
)
from .containers import MAIN_ROLE, SIDECAR_ROLE, validate_pod_containers
from .errors import fail

# 模板级字段(留在模板表;容器级由容器表水合为统一 canonical,见
# container_spec.build_canonical)。三段式契约的 template dict 只认这些键 +
# main_container_id/sidecar_container_ids/volumes;与 legacy 内联容器键
# 并存 = mixed 形态 → 400。
TEMPLATE_LEVEL_FIELDS: tuple[str, ...] = (
    "template_id", "template_name", "description", "data",
    "namespace", "node_name", "fs_group", "pod_name", "sse_path",
    "ready_timeout", "ready_poll_interval", "kubeconfig",
    "scope_concurrency", "pod_concurrency", "session_ttl", "pod_ttl",
    "min_idle_pods",
)

# 模板级中按整数解析的字段(DB 列为整型,wire 允许数字字符串)
TEMPLATE_INT_FIELDS = frozenset({
    "ready_timeout", "ready_poll_interval", "min_idle_pods", "pod_concurrency",
    "pod_ttl", "scope_concurrency", "session_ttl",
    "fs_group",
})

# legacy 内联容器键(2026-08 拆表前平铺在模板上的字段;三段式 wire 独占后
# 只作为 mixed-400 检测的黑名单存在——出现在 template dict 即拒绝)
LEGACY_INLINE_CONTAINER_KEYS = frozenset({
    "agent_image", "run_as_user", "run_as_group", "container_name",
    "container_port", "port_name", "sse_port", "health_path", "agent_env",
    "agent_env_from", "image_pull_policy", "readiness_initial_delay",
    "readiness_period", "nfs_server", "nfs_path", "nfs_mount_path",
    "agent_cpu_request", "agent_memory_request", "agent_cpu_limit",
    "agent_memory_limit", "sidecars", "agent_host_path_mounts",
    "agent_configmap_mounts", "agent_pvc_mounts", "agent_nfs_mounts",
    "command", "args",
})

# 模板级 wire 键别名:K8s 派生字段用 K8s 拼写(nodeName);snake 双形态拒绝
# (防静默二义——两个拼写同时给不同值无法仲裁,fail-fast)
TEMPLATE_WIRE_ALIASES = {"node_name": "nodeName",
                         "fs_group": "fsGroup"}

# 策略字段下界(0/负值 = 拒绝服务配置:pod_concurrency=0 会部满 max_pods 个
# 必然用不上的 Pod 后永久 scope_full)
_POLICY_MINIMUMS = {
    "scope_concurrency": 1,
    "pod_concurrency": 1,
    "session_ttl": 1,
    "pod_ttl": 1,
    "min_idle_pods": 0,
    "ready_timeout": 1,
}

# 节点名按宽松 hostname 形态(字母/数字/'-'/'.';K8s 节点名 = DNS subdomain,
# 云厂商 FQDN 形态带 '.',不宜收紧到 DNS label)
_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def hydrate_containers(
        main_spec: dict[str, Any],
        sidecar_specs: list[dict[str, Any]],
        volumes: dict[str, dict[str, Any]],
        where: str,
) -> dict[str, Any]:
    """容器内部规范形 + 模板 volumes → ``{main_container, sidecars}``(canonical)。

    读路径(行水合)与写路径(载荷解析)共用的唯一水合出口:build_canonical
    ×2 → validate_pod_containers(≤8/重名/撞端口/挂载冲突)。
    """
    main = build_canonical(main_spec, volumes, where, role=MAIN_ROLE)
    sidecars = [build_canonical(spec, volumes, where, role=SIDECAR_ROLE)
                for spec in sidecar_specs]
    main, sidecars = validate_pod_containers(main, sidecars, where)
    return {"main_container": main, "sidecars": sidecars}


def _validate_policy_fields(template_id: str, kwargs: dict[str, Any]) -> None:
    problems: list[str] = []
    for field, minimum in _POLICY_MINIMUMS.items():
        value = kwargs.get(field)
        if isinstance(value, int) and value < minimum:
            problems.append(f"{field}={value} < {minimum}")
    if problems:
        fail(
            f"template {template_id!r} policy fields invalid: {'; '.join(problems)}"
        )


def _validate_pod_placing_fields(template_id: str, kwargs: dict[str, Any]) -> None:
    """节点绑定字段(node_name)校验(run_as 已随容器 canonical 校验)。

    坏节点名要到 K8s API 侧才失败(Pod 永久 Pending 挂满 ready_timeout,
    错误对下发方不可见)——提前到 config_sync 锁外,确定性 400。
    """
    for field in ("fs_group",):
        value = kwargs.get(field)
        if isinstance(value, int) and value < 0:
            fail(
                f"template {template_id!r} {field}={value} must be >= 0")
    node_name = kwargs.get("node_name")
    if node_name is not None and (
            not isinstance(node_name, str)
            or len(node_name) > 253
            or not _NODE_NAME_RE.fullmatch(node_name)):
        fail(
            f"template {template_id!r} node_name={node_name!r} must be a "
            "hostname (letters/digits/'-'/'.'; no spaces), <=253 chars")


def template_split_kwargs(
        template_id: str,
        payload: dict[str, Any],
        containers_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """三段式 template dict(容器引用形态)→ (Template 构造 kwargs, 内部卷映射)。

    模板级字段沿用既有校验(int 严格/策略下界/node_name hostname);主容器/
    sidecar 规格来自已解析容器规范形(container_spec),挂载经 volumes join;
    mixed 形态(引用键与 legacy 内联容器键并存)→ 400。

    只返回载荷**显式给出**的模板级字段——缺省字段由调用方构造 ``Template``
    时按 dataclass 默认值补齐;返回卷映射供调用方落 volumes 列。
    """
    inline = {k for k in payload if k in LEGACY_INLINE_CONTAINER_KEYS}
    if inline:
        fail(
            f"template {template_id!r} mixes container references with inline "
            f"legacy keys {sorted(inline)}; container config belongs in the "
            "containers section"
        )
    kwargs: dict[str, Any] = {"template_id": template_id}
    for field in TEMPLATE_LEVEL_FIELDS:
        if field == "template_id":
            continue
        wire_key = TEMPLATE_WIRE_ALIASES.get(field, field)
        if wire_key != field and payload.get(field) is not None:
            fail(
                f"template {template_id!r}: use the k8s wire spelling "
                f"{wire_key!r} for {field!r} in the split contract")
        if payload.get(wire_key) is not None:
            value = payload[wire_key]
            if field in TEMPLATE_INT_FIELDS:
                if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    fail(
                        f"template field {field} must be an integer, got {value!r}"
                    )
                try:
                    value = int(value)
                except ValueError:
                    fail(
                        f"template field {field} must be an integer, got {value!r}"
                    )
            kwargs[field] = value
    _validate_policy_fields(template_id, kwargs)
    if kwargs.get("node_name") == "":
        kwargs["node_name"] = None
    _validate_pod_placing_fields(template_id, kwargs)

    main_cid = payload.get("main_container_id")
    if not isinstance(main_cid, str) or not main_cid.strip():
        fail(
            f"template {template_id!r} requires a non-empty main_container_id"
        )
    main_spec = containers_by_id.get(main_cid)
    if main_spec is None:
        fail(
            f"template {template_id!r} main_container_id {main_cid!r} not "
            "present in the containers section"
        )
    volumes = canonical_volumes(
        payload.get("volumes"), f"template {template_id!r}.volumes")

    sidecar_ids = payload.get("sidecar_container_ids")
    if sidecar_ids is None:
        sidecar_ids = []
    if not isinstance(sidecar_ids, list) or any(
            not isinstance(cid, str) for cid in sidecar_ids):
        fail(
            f"template {template_id!r} sidecar_container_ids must be a list "
            f"of container ids, got {sidecar_ids!r}"
        )
    if len(set(sidecar_ids)) != len(sidecar_ids):
        fail(
            f"template {template_id!r} sidecar_container_ids has duplicates: "
            f"{sidecar_ids!r}"
        )
    sidecar_specs = []
    for cid in sidecar_ids:
        spec = containers_by_id.get(cid)
        if spec is None:
            fail(
                f"template {template_id!r} sidecar_container_id {cid!r} not "
                "present in the containers section"
            )
        sidecar_specs.append(spec)

    # 未挂载卷(fail-fast,与未被引用容器对称:全量语义下 = 配置错误)
    mounted = mounted_volume_names(main_spec)
    for spec in sidecar_specs:
        mounted |= mounted_volume_names(spec)
    unused = sorted(set(volumes) - mounted)
    if unused:
        fail(
            f"template {template_id!r} volumes not mounted by any container: "
            f"{unused}"
        )

    where = f"template {template_id!r}"
    kwargs.update(hydrate_containers(main_spec, sidecar_specs, volumes, where))
    return kwargs, volumes
