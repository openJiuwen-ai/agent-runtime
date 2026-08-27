# coding: utf-8
"""容器卷挂载(hostPath / ConfigMap / PVC)——SM 校验/归一 + RM 渲染共享。

主 agent 容器(Template ``agent_host_path_mounts`` / ``agent_configmap_mounts`` /
``agent_pvc_mounts``)与 sidecar 容器(sidecars 各自的 ``host_path_mounts`` /
``configmap_mounts`` / ``pvc_mounts``)共用同一套规范形与校验;SM 与 RM 共用本
模块,不引入 SM↔RM 相互 import(与 spec_fields/sidecars 同款顶层共享先例)。

指纹不变式(★,同 sidecars.py):规范形填满全部默认键 + 列表按 mount_path 升序;
「显式给默认值」与「省略键」、「下发顺序重排」必须产生同一 deploy_ver。
None 与空列表统一为 None——util.fingerprint 只滤 None。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .errors import InvalidParams

# K8s 资源名(ConfigMap/PVC):DNS subdomain,取保守子集校验(非空 + 长度)
_K8S_RESOURCE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_MAX_RESOURCE_NAME_LEN = 253

_HOST_PATH_TYPES = frozenset({
    "Directory", "DirectoryOrCreate", "File", "FileOrCreate",
    "Socket", "CharDevice", "BlockDevice",
})

_MOUNT_KEYS_BY_TYPE = {
    "host_path_mounts": frozenset(
        {"host_path", "mount_path", "read_only", "host_path_type"}),
    "configmap_mounts": frozenset(
        {"config_map_name", "mount_path", "sub_path", "items", "read_only"}),
    "pvc_mounts": frozenset({"claim_name", "mount_path", "read_only"}),
}


def _check_mount_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise InvalidParams(
            f"{where} requires an absolute mount_path, got {value!r}")
    return value


def _check_bool(value: Any, where: str, key: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise InvalidParams(f"{where}.{key} must be a boolean, got {value!r}")
    return value


def _check_sub_path(value: Any, where: str) -> Optional[str]:
    """sub_path / items[].path:相对路径(不得以 / 开头,K8s subPath 语义)。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise InvalidParams(
            f"{where} must be a non-empty relative path (no leading '/'), "
            f"got {value!r}")
    return value


def _check_resource_name(value: Any, where: str, key: str) -> str:
    if (not isinstance(value, str)
            or not _K8S_RESOURCE_NAME_RE.match(value)
            or len(value) > _MAX_RESOURCE_NAME_LEN):
        raise InvalidParams(
            f"{where}.{key} must be a valid k8s resource name "
            f"(lowercase alphanumeric, '-' or '.'), got {value!r}")
    return value


def _unknown_keys(item: dict, kind: str, where: str) -> None:
    unknown = set(item) - _MOUNT_KEYS_BY_TYPE[kind]
    if unknown:
        raise InvalidParams(
            f"{where} unknown keys {sorted(unknown)}; allowed: "
            f"{sorted(_MOUNT_KEYS_BY_TYPE[kind])}"
        )


def _sorted_mounts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 mount_path 升序(挂载顺序无语义,规范形消除顺序敏感)。"""
    return sorted(items, key=lambda m: m["mount_path"])


# -------------------------------------------------------------- hostPath

def canonical_host_path_mounts(value: Any, where: str) -> list[dict[str, Any]]:
    """hostPath 挂载 → 规范形;非法 raise InvalidParams。"""
    if not isinstance(value, list):
        raise InvalidParams(f"{where} must be a list, got {value!r}")
    mounts: list[dict[str, Any]] = []
    for j, item in enumerate(value):
        mount_where = f"{where}[{j}]"
        if not isinstance(item, dict):
            raise InvalidParams(f"{mount_where} must be an object, got {item!r}")
        _unknown_keys(item, "host_path_mounts", mount_where)
        host_path = item.get("host_path")
        if not isinstance(host_path, str) or not host_path.startswith("/"):
            raise InvalidParams(
                f"{mount_where} requires an absolute host_path, got {host_path!r}")
        host_path_type = item.get("host_path_type")
        if host_path_type is not None and host_path_type not in _HOST_PATH_TYPES:
            raise InvalidParams(
                f"{mount_where}.host_path_type must be one of "
                f"{sorted(_HOST_PATH_TYPES)} or null, got {host_path_type!r}")
        mounts.append({
            "host_path": host_path,
            "mount_path": _check_mount_path(item.get("mount_path"), mount_where),
            "read_only": _check_bool(item.get("read_only"), mount_where,
                                     "read_only", False),
            "host_path_type": host_path_type,
        })
    return _sorted_mounts(mounts)


# -------------------------------------------------------------- ConfigMap

def canonical_configmap_mounts(value: Any, where: str) -> list[dict[str, Any]]:
    """ConfigMap 挂载 → 规范形(沿老 SDK ConfigMapMount 语义:
    config_map_name/mount_path/sub_path/items/read_only);非法 raise InvalidParams。"""
    if not isinstance(value, list):
        raise InvalidParams(f"{where} must be a list, got {value!r}")
    mounts: list[dict[str, Any]] = []
    for j, item in enumerate(value):
        mount_where = f"{where}[{j}]"
        if not isinstance(item, dict):
            raise InvalidParams(f"{mount_where} must be an object, got {item!r}")
        _unknown_keys(item, "configmap_mounts", mount_where)
        name = _check_resource_name(item.get("config_map_name"),
                                    mount_where, "config_map_name")
        sub_path = _check_sub_path(item.get("sub_path"), f"{mount_where}.sub_path")
        items_raw = item.get("items")
        items: Optional[list[dict[str, str]]] = None
        if items_raw is not None:
            if not isinstance(items_raw, list):
                raise InvalidParams(
                    f"{mount_where}.items must be a list of "
                    "{{key, path}} objects, got {items_raw!r}")
            items = []
            for k, entry in enumerate(items_raw):
                entry_where = f"{mount_where}.items[{k}]"
                if not isinstance(entry, dict) or set(entry) != {"key", "path"}:
                    raise InvalidParams(
                        f"{entry_where} must be an object with exactly "
                        f"keys 'key' and 'path', got {entry!r}")
                key = entry["key"]
                if not isinstance(key, str) or not key:
                    raise InvalidParams(
                        f"{entry_where}.key must be a non-empty string, got {key!r}")
                items.append({"key": key,
                              "path": _check_sub_path(entry["path"], entry_where)})
            items.sort(key=lambda e: e["key"])
        mounts.append({
            "config_map_name": name,
            "mount_path": _check_mount_path(item.get("mount_path"), mount_where),
            "sub_path": sub_path,
            "items": items,
            "read_only": _check_bool(item.get("read_only"), mount_where,
                                     "read_only", True),
        })
    return _sorted_mounts(mounts)


# -------------------------------------------------------------- PVC

def canonical_pvc_mounts(value: Any, where: str) -> list[dict[str, Any]]:
    """PVC 挂载 → 规范形;非法 raise InvalidParams。"""
    if not isinstance(value, list):
        raise InvalidParams(f"{where} must be a list, got {value!r}")
    mounts: list[dict[str, Any]] = []
    for j, item in enumerate(value):
        mount_where = f"{where}[{j}]"
        if not isinstance(item, dict):
            raise InvalidParams(f"{mount_where} must be an object, got {item!r}")
        _unknown_keys(item, "pvc_mounts", mount_where)
        mounts.append({
            "claim_name": _check_resource_name(item.get("claim_name"),
                                               mount_where, "claim_name"),
            "mount_path": _check_mount_path(item.get("mount_path"), mount_where),
            "read_only": _check_bool(item.get("read_only"), mount_where,
                                     "read_only", False),
        })
    return _sorted_mounts(mounts)


# -------------------------------------------------------------- 归一/校验入口

def normalize_mounts(
        value: Any, kind: str,
) -> Optional[list[dict[str, Any]]]:
    """宽容归一(读路径防御,不抛):None/[]/非 list → None;坏项静默丢弃;
    产物 = 规范形 + 按 mount_path 排序;空 → None。"""
    canonical = {
        "host_path_mounts": canonical_host_path_mounts,
        "configmap_mounts": canonical_configmap_mounts,
        "pvc_mounts": canonical_pvc_mounts,
    }[kind]
    if not isinstance(value, list):
        return None
    items = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            items.extend(canonical([item], "mounts[n]"))
        except InvalidParams:
            continue
    return _sorted_mounts(items) or None


def find_mount_path_conflicts(
        mount_lists: list[tuple[str, list[dict[str, Any]] | None]],
        extra_paths: Optional[list[str]] = None,
) -> Optional[str]:
    """同一容器内 mount_path 重复检测(K8s 会拒,这里 fail-fast 到 400)。

    mount_lists: [(来源标签, 挂载规范形列表)];extra_paths: 该容器既有的其他
    挂载点(如主容器的 nfs_mount_path)。
    """
    seen: dict[str, str] = {}
    for source, mounts in mount_lists:
        for m in mounts or []:
            path = m["mount_path"]
            if path in seen:
                return (f"mount_path {path!r} duplicated in {seen[path]} and "
                        f"{source}; a container can mount a path only once")
            seen[path] = source
    for path in extra_paths or []:
        if path in seen:
            return (f"mount_path {path!r} duplicated in {seen[path]} and "
                    f"nfs_mount_path; a container can mount a path only once")
    return None


def validate_agent_mounts(
        host_path: Any, config_map: Any, pvc: Any, *,
        nfs_mount_path: Optional[str],
) -> tuple[Optional[list], Optional[list], Optional[list]]:
    """主容器三列表 config_sync 下发校验(fail-fast 400);各返回规范形或 None。"""
    out = []
    for kind, value, where in (
            ("host_path_mounts", host_path, "agent_host_path_mounts"),
            ("configmap_mounts", config_map, "agent_configmap_mounts"),
            ("pvc_mounts", pvc, "agent_pvc_mounts"),
    ):
        if value is None:
            out.append(None)
            continue
        if not isinstance(value, list):
            raise InvalidParams(f"{where} must be a list, got {value!r}")
        out.append({
            "host_path_mounts": canonical_host_path_mounts,
            "configmap_mounts": canonical_configmap_mounts,
            "pvc_mounts": canonical_pvc_mounts,
        }[kind](value, where) or None)
    conflict = find_mount_path_conflicts(
        [("agent_host_path_mounts", out[0]),
         ("agent_configmap_mounts", out[1]),
         ("agent_pvc_mounts", out[2])],
        extra_paths=[nfs_mount_path] if nfs_mount_path else None,
    )
    if conflict:
        raise InvalidParams(conflict)
    return out[0] or None, out[1] or None, out[2] or None
