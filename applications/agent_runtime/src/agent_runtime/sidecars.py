# coding: utf-8
"""template.sidecars —— 同 Pod sidecar 容器规格(SM 校验/归一 + RM 渲染兜底共享)。

通用 sidecar 列表(单 JSON DB 列 ``sidecars``),每项一个容器规格 dict;
jiuwenbox 是第一个使用者(与主 agent 容器同 Pod、共享网络命名空间,
agent 经 127.0.0.1:port 访问)。SM 与 RM 共用本模块,不引入 SM↔RM 相互 import
(与 spec_fields.py 同款的顶层共享先例)。

指纹不变式(★):规范形 = 每项填满全部默认键 + 列表按 name 升序。
「显式给默认值」与「省略键」、「下发顺序重排」、「DB JSON 列键序重排」
必须产生同一 deploy_ver,否则会造成伪 A 类日落(2026-08-26 缺陷④教训:
MySQL JSON 列回读键序重排曾使暖 Pod 复用失效)。None 与空列表统一为 None
——util.fingerprint 只滤 None,以 [] 为默认会使全部存量模板指纹变化。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .errors import InvalidParams
from .mounts import (
    canonical_configmap_mounts,
    canonical_host_path_mounts,
    canonical_pvc_mounts,
    check_resource_name,
    find_mount_path_conflicts,
)

# K8s 容器名:DNS-1123 label(小写字母数字与 '-',首尾须字母数字,≤63)
SIDECAR_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
SIDECAR_MAX = 8  # 单 Pod sidecar 条数上限(防御性,当前用户只需 1)
_PROBE_TYPES = frozenset({"tcp", "http"})
_MAX_IMAGE_LEN = 512
# envFrom prefix:K8s env 变量名前缀(C_IDENTIFIER 前缀语义)
_ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 单项合法键(_canonical_sidecar 拒未知键:sidecar 是安全敏感面,
# 拼错的 capabilities_add 被静默吞掉 = "看似有特权实际没有"的运行期疑难)
_SIDECAR_KEYS = frozenset({
    "name", "image", "port", "env", "env_from", "image_pull_policy",
    "cpu_request", "memory_request", "cpu_limit", "memory_limit",
    "privileged", "capabilities_add", "capabilities_drop",
    "seccomp_unconfined", "apparmor_unconfined", "run_as_user", "run_as_group",
    "host_path_mounts", "configmap_mounts", "pvc_mounts",
    "readiness_probe_type", "readiness_path",
    "readiness_initial_delay", "readiness_period", "readiness_timeout_seconds",
})

_ENV_FROM_ITEM_KEYS = frozenset({"prefix", "secret_ref", "config_map_ref"})
_ENV_FROM_REF_KEYS = frozenset({"secret_ref", "config_map_ref"})


def canonical_env_from(value: Any, where: str) -> Optional[list[dict[str, Any]]]:
    """envFrom → 内部规范形(secretRef/configMapRef 引用,值不落模板)。

    输入(内部 snake 形态;K8s wire 的 camelCase envFrom 由
    session_manager/container_spec.py 翻译后再进来):
    ``[{prefix?, secret_ref|config_map_ref: {name, optional?}}]``
    规范形:``[{prefix: str|None, <ref>: {name, optional}}]``;None/[] → None。

    sidecar 规范形以**条件键**携带 ``env_from``(None 省略键)——与其他
    显式存 None 的键不同:env_from 是后加的,显式存 None 会改全部存量
    sidecar 的指纹 → 伪 A 类日落。
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where} must be a list of envFrom sources, got {value!r}")
    if not value:
        return None
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        item_where = f"{where}[{i}]"
        if not isinstance(item, dict):
            raise InvalidParams(f"{item_where} must be an object, got {item!r}")
        unknown = set(item) - _ENV_FROM_ITEM_KEYS
        if unknown:
            raise InvalidParams(
                f"{item_where} unknown keys {sorted(unknown)}; allowed: "
                f"{sorted(_ENV_FROM_ITEM_KEYS)}")
        refs = [k for k in _ENV_FROM_REF_KEYS if item.get(k) is not None]
        if len(refs) != 1:
            raise InvalidParams(
                f"{item_where} requires exactly one of secret_ref/config_map_ref, "
                f"got {item!r}")
        ref_key = refs[0]
        ref = item[ref_key]
        if not isinstance(ref, dict):
            raise InvalidParams(
                f"{item_where}.{ref_key} must be an object, got {ref!r}")
        ref_unknown = set(ref) - {"name", "optional"}
        if ref_unknown:
            raise InvalidParams(
                f"{item_where}.{ref_key} unknown keys {sorted(ref_unknown)}; "
                "allowed: ['name', 'optional']")
        name = check_resource_name(ref.get("name"), f"{item_where}.{ref_key}")
        optional = ref.get("optional", False)
        if not isinstance(optional, bool):
            raise InvalidParams(
                f"{item_where}.{ref_key}.optional must be a boolean, "
                f"got {optional!r}")
        prefix = item.get("prefix")
        if prefix is not None and (
                not isinstance(prefix, str) or not _ENV_PREFIX_RE.match(prefix)):
            raise InvalidParams(
                f"{item_where}.prefix must be an env-var-name prefix "
                f"(letters/digits/'_', leading letter or '_'), got {prefix!r}")
        out.append({"prefix": prefix,
                    ref_key: {"name": name, "optional": optional}})
    return out


def _canonical_env(value: Any, where: str) -> dict[str, str]:
    """env 校验(与 config_store 的 agent_env 同规则)→ 全 str 化 dict。"""
    if not isinstance(value, dict) or any(
            not isinstance(k, str) or isinstance(v, (list, dict)) or v is None
            for k, v in value.items()):
        raise InvalidParams(
            f"{where}.env must be an object mapping string keys to "
            f"scalar values, got {value!r}"
        )
    return {k: str(v) for k, v in value.items()}


def _canonical_int(value: Any, where: str, key: str, *, minimum: int,
                   maximum: int | None = None) -> int:
    """int 字段校验(不接受 bool——bool 是 int 子类,显式排除)。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(f"{where}.{key} must be an integer, got {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"(0, {maximum}]" if maximum is not None else f">= {minimum}"
        raise InvalidParams(f"{where}.{key} must be an integer in {bound}, got {value!r}")
    return value


def _canonical_str(value: Any, where: str, key: str, *, max_len: int,
                   required: bool = True) -> Optional[str]:
    """str 字段校验;required=False 时 None/缺省原样返回(None)。"""
    if value is None:
        if required:
            raise InvalidParams(f"{where}.{key} requires a non-empty string")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise InvalidParams(
            f"{where}.{key} must be a non-empty string of at most "
            f"{max_len} chars, got {value!r}"
        )
    return value


def _canonical_bool(value: Any, where: str, key: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidParams(f"{where}.{key} must be a boolean, got {value!r}")
    return value


def _canonical_caps(value: Any, where: str, key: str) -> list[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value):
        raise InvalidParams(
            f"{where}.{key} must be a list of non-empty strings, got {value!r}"
        )
    return list(value)


def _canonical_sidecar(item: Any, where: str) -> dict[str, Any]:
    """单项 sidecar dict → 规范形(填满全部默认键);非法 raise InvalidParams。

    消息带 ``sidecars[{i}]`` 定位,风格对齐 config_store 的 agent_env 校验。
    """
    if not isinstance(item, dict):
        raise InvalidParams(f"{where} must be an object, got {item!r}")
    unknown = set(item) - _SIDECAR_KEYS
    if unknown:
        raise InvalidParams(
            f"{where} unknown keys {sorted(unknown)}; allowed: "
            f"{sorted(_SIDECAR_KEYS)}"
        )

    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise InvalidParams(f"{where} requires a non-empty string name")
    if not SIDECAR_NAME_RE.match(name):
        raise InvalidParams(
            f"{where}.name {name!r} must be a DNS-1123 label "
            "(lowercase alphanumeric or '-'), max 63 chars"
        )
    image = _canonical_str(item.get("image"), where, "image", max_len=_MAX_IMAGE_LEN)

    port = item.get("port")
    if port is not None:
        port = _canonical_int(port, where, "port", minimum=1, maximum=65535)

    probe_type = item.get("readiness_probe_type")
    if probe_type is not None and probe_type not in _PROBE_TYPES:
        raise InvalidParams(
            f"{where}.readiness_probe_type must be 'tcp' or 'http', got {probe_type!r}"
        )
    if probe_type is not None and port is None:
        raise InvalidParams(f"{where} requires port when readiness_probe_type is set")

    run_as_user = item.get("run_as_user")
    if run_as_user is not None:
        run_as_user = _canonical_int(run_as_user, where, "run_as_user", minimum=0)
    run_as_group = item.get("run_as_group")
    if run_as_group is not None:
        run_as_group = _canonical_int(run_as_group, where, "run_as_group", minimum=0)
    # envFrom:None/[] 归一 None(条件键,见 canonical_env_from docstring)
    env_from = canonical_env_from(item.get("env_from"), f"{where}.env_from")

    result = {
        "name": name,
        "image": image,
        "port": port,
        "env": _canonical_env(item.get("env") or {}, where),
        "image_pull_policy": _canonical_str(
            item.get("image_pull_policy") or "IfNotPresent",
            where, "image_pull_policy", max_len=64),
        "cpu_request": _canonical_str(
            item.get("cpu_request"), where, "cpu_request",
            max_len=32, required=False),
        "memory_request": _canonical_str(
            item.get("memory_request"), where, "memory_request",
            max_len=32, required=False),
        "cpu_limit": _canonical_str(
            item.get("cpu_limit"), where, "cpu_limit",
            max_len=32, required=False),
        "memory_limit": _canonical_str(
            item.get("memory_limit"), where, "memory_limit",
            max_len=32, required=False),
        "privileged": _canonical_bool(item.get("privileged") or False,
                                      where, "privileged"),
        "capabilities_add": _canonical_caps(
            item.get("capabilities_add") or [], where, "capabilities_add"),
        "capabilities_drop": _canonical_caps(
            item.get("capabilities_drop") or [], where, "capabilities_drop"),
        "seccomp_unconfined": _canonical_bool(
            item.get("seccomp_unconfined") or False, where, "seccomp_unconfined"),
        "apparmor_unconfined": _canonical_bool(
            item.get("apparmor_unconfined") or False, where, "apparmor_unconfined"),
        "run_as_user": run_as_user,
        "run_as_group": run_as_group,
        "host_path_mounts": canonical_host_path_mounts(
            item.get("host_path_mounts") or [], f"{where}.host_path_mounts"),
        "configmap_mounts": canonical_configmap_mounts(
            item.get("configmap_mounts") or [], f"{where}.configmap_mounts"),
        "pvc_mounts": canonical_pvc_mounts(
            item.get("pvc_mounts") or [], f"{where}.pvc_mounts"),
        "readiness_probe_type": probe_type,
        "readiness_path": _canonical_str(
            item.get("readiness_path") or "/health",
            where, "readiness_path", max_len=128),
        "readiness_initial_delay": _canonical_int(
            item.get("readiness_initial_delay") if item.get("readiness_initial_delay") is not None else 5,
            where, "readiness_initial_delay", minimum=0),
        "readiness_period": _canonical_int(
            item.get("readiness_period") if item.get("readiness_period") is not None else 10,
            where, "readiness_period", minimum=1),
        "readiness_timeout_seconds": _canonical_int(
            item.get("readiness_timeout_seconds") if item.get("readiness_timeout_seconds") is not None else 3,
            where, "readiness_timeout_seconds", minimum=1, maximum=300),
    }
    # env_from 条件键:有值才出现(存量 sidecar 指纹零扰动)
    if env_from is not None:
        result["env_from"] = env_from
    return result


def _sorted_canonical(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 name 升序排列(指纹对列表顺序稳定;name 已保证唯一性由调用方校验)。"""
    return sorted(items, key=lambda sc: sc["name"])


def normalize_sidecars(value: Any) -> Optional[list[dict[str, Any]]]:
    """宽容归一(读路径防御,不抛异常):None/[]/非 list → None;
    非 dict 项或缺 name/image 的项静默丢弃;其余项走规范形后按 name 排序;
    结果为空 → None。与 template_from_row 的 agent_env 兜底同一语义。"""
    if not isinstance(value, list):
        return None
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("name"), str) or not item.get("name"):
            continue
        if not isinstance(item.get("image"), str) or not item.get("image"):
            continue
        try:
            items.append(_canonical_sidecar(item, "sidecars[n]"))
        except InvalidParams:
            continue
    return _sorted_canonical(items) or None


def validate_sidecars(
        value: Any,
        *,
        container_name: str,
        sse_port: int,
        container_port: int,
) -> Optional[list[dict[str, Any]]]:
    """config_sync 下发校验(fail-fast 400);合法返回规范形列表,
    None/空列表 → None。跨字段校验(端口/容器名冲突)委托 find_sidecar_conflict。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidParams(
            f"sidecars must be a list of container objects, got {value!r}"
        )
    if len(value) > SIDECAR_MAX:
        raise InvalidParams(
            f"sidecars must have at most {SIDECAR_MAX} entries, got {len(value)}"
        )
    items = [
        _canonical_sidecar(item, f"sidecars[{i}]") for i, item in enumerate(value)
    ]
    names = [sc["name"] for sc in items]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise InvalidParams(f"sidecars duplicate container names: {dupes}")
    conflict = find_sidecar_conflict(items, container_name, sse_port, container_port)
    if conflict:
        raise InvalidParams(f"sidecars: {conflict}")
    # 每个 sidecar 自身三种挂载的 mount_path 不得重复(K8s 会拒,这里 fail-fast)
    for i, sc in enumerate(items):
        mount_conflict = find_mount_path_conflicts([
            (f"sidecars[{i}].host_path_mounts", sc["host_path_mounts"]),
            (f"sidecars[{i}].configmap_mounts", sc["configmap_mounts"]),
            (f"sidecars[{i}].pvc_mounts", sc["pvc_mounts"]),
        ])
        if mount_conflict:
            raise InvalidParams(f"sidecars[{i}]: {mount_conflict}")
    return _sorted_canonical(items) or None


def find_sidecar_conflict(
        sidecars: list[dict[str, Any]],
        container_name: str,
        sse_port: int,
        container_port: int,
) -> Optional[str]:
    """纯谓词:返回首个冲突描述(SM 包 InvalidParams、RM 包 DeployFailed 共用)。

    - sidecar name == 主容器 container_name(K8s 同 Pod 容器名必须唯一)
    - sidecar port 撞 sse_port / container_port / 兄弟 sidecar port
      (同 Pod 共享网络命名空间,agent 经 127.0.0.1:port 访问 sidecar,
      撞号几乎必然是配错——有意的严格)
    """
    for i, sc in enumerate(sidecars):
        if sc["name"] == container_name:
            return (f"sidecars[{i}].name {sc['name']!r} conflicts with the "
                    f"agent container_name {container_name!r}")
    agent_ports = {p for p in (sse_port, container_port) if p}
    seen: dict[int, str] = {}
    for i, sc in enumerate(sidecars):
        port = sc.get("port")
        if not port:
            continue
        if port in agent_ports:
            return (f"sidecars[{i}].port {port} conflicts with the agent "
                    f"container ports {sorted(agent_ports)}; sidecar ports must "
                    "differ from sse_port/container_port and each other")
        if port in seen:
            return (f"sidecars[{i}].port {port} conflicts with "
                    f"{seen[port]}; sidecar ports must differ from each other")
        seen[port] = f"sidecars[{i}].port"
    return None
