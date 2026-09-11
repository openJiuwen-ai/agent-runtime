# coding: utf-8
"""容器规范形(单一 canonical,主/sidecar 共用)——SM 校验/水合 + RM 渲染共享。

2026-09 统一重构:主容器与 sidecar 共用一个 canonical 形(13 键,role 不影响
键集,只影响值域与默认值)。此前主容器是 Template 扁平字段、sidecar 是
sidecars.py 24 键两套形状——2026-08 拆表时为保 deploy_ver 指纹连续而保留的
历史包袱;开发阶段无生产存量,指纹一次性重置换「加容器字段只改一处」
(canonical 定义 + 渲染分支,spec_fields/Template/投影不再逐字段重复)。

canonical 13 键::

    name image image_pull_policy ports env env_from resources
    host_path_mounts configmap_mounts pvc_mounts nfs
    security_context readiness_probe

- ``ports``:``[{name: str|None, container_port: int}] | None``。main 恰一个
  ``name="sse"``(gateway 直连契约)+ 至多一个 ``name="http"``,固定序
  ``[sse, http?]``;sidecar 至多一个**无名**端口(纯声明性)。
- ``nfs``:``{server, path, mount_path} | None``,main 独有(sidecar 恒 None)。
- ``security_context``:7 键恒满;main 值域仅 runAs 两键(其余恒默认),sidecar
  全量。``readiness_probe``:5 键恒满;main 恒 http 且 ``timeout=None``
  (= 不支持,非未设置),sidecar 可 tcp/http 缺省带 timeout(period 默认
  main 5 / sidecar 10,历史默认不得拉平)。

指纹不变式(★):canonical 幂等(无等价异形)、全键填满(无条件键)、
容器间按 name 升序、None/[] 统一 None(容器列表层)、容器内列表恒列表。
「显式给默认值」与「省略键」、「下发顺序重排」、「DB JSON 列键序重排」
必须产生同一 deploy_ver(util.fingerprint 滤 None + sort_keys 递归;
2026-08-26 缺陷④教训:MySQL JSON 列回读键序重排曾使暖 Pod 复用失效)。

role 语义由 ``MAIN_ROLE``/``SIDECAR_ROLE`` 区分:canonical_container(校验
水合/fail-fast)与 normalize_container(读路径防御,坏值回退)是同一形状的
两道门,与 mounts.py 的 canonical_*/normalize_mounts 同款分工。

SM 与 RM 共用本模块,不引入 SM↔RM 相互 import(与 spec_fields/mounts 同款
顶层共享先例)。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .errors import InvalidParams
from .mounts import (
    canonical_configmap_mounts,
    canonical_host_path_mounts,
    canonical_nfs_mounts,
    canonical_pvc_mounts,
    check_resource_name,
    find_mount_path_conflicts,
)

logger = logging.getLogger(__name__)

MAIN_ROLE = "main"
SIDECAR_ROLE = "sidecar"

# K8s 容器名:DNS-1123 label(小写字母数字与 '-',首尾须字母数字,≤63)
CONTAINER_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
SIDECAR_MAX = 8  # 单 Pod sidecar 条数上限(防御性,当前用户只需 1)
_PROBE_TYPES = frozenset({"tcp", "http"})
_MAX_IMAGE_LEN = 512
_MAX_PATH_LEN = 256
_PROBE_PATH_MAX = 128
# envFrom prefix:K8s env 变量名前缀(C_IDENTIFIER 前缀语义)
_ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ---- 默认值单源(★ 加字段/改默认只动这里 + 渲染分支)----
DEFAULT_IMAGE_PULL_POLICY = "IfNotPresent"
DEFAULT_MAIN_NAME = "agent"
DEFAULT_SSE_PORT = 8080
MAIN_PORTS_DEFAULT: list[dict[str, Any]] = [
    {"name": "sse", "container_port": DEFAULT_SSE_PORT}]
MAIN_PROBE_DEFAULT: dict[str, Any] = {
    "probe_type": "http", "path": "/health",
    "initial_delay": 5, "period": 5, "timeout": None}
SIDECAR_PROBE_DEFAULT: dict[str, Any] = {
    "probe_type": None, "path": "/health",
    "initial_delay": 5, "period": 10, "timeout": 3}
SECCTX_DEFAULT: dict[str, Any] = {
    "run_as_user": None, "run_as_group": None, "privileged": False,
    "capabilities_add": [], "capabilities_drop": [],
    "seccomp_unconfined": False, "apparmor_unconfined": False}
RESOURCES_DEFAULT: dict[str, Any] = {
    "cpu_request": None, "memory_request": None,
    "cpu_limit": None, "memory_limit": None}
_RESOURCE_KEYS = tuple(RESOURCES_DEFAULT)

# canonical 合法键(_canonical_container 拒未知键:容器是安全敏感面,
# 拼错的 capabilities_add 被静默吞掉 = "看似有特权实际没有"的运行期疑难)
_CONTAINER_KEYS = frozenset({
    "name", "image", "image_pull_policy", "command", "args", "ports", "env",
    "env_from", "resources", "host_path_mounts", "configmap_mounts",
    "pvc_mounts", "nfs_mounts", "security_context", "readiness_probe",
})

_ENV_FROM_ITEM_KEYS = frozenset({"prefix", "secret_ref", "config_map_ref"})
_ENV_FROM_REF_KEYS = frozenset({"secret_ref", "config_map_ref"})
_PROBE_KEYS = frozenset(
    {"probe_type", "path", "initial_delay", "period", "timeout"})
_SECCTX_KEYS = frozenset(SECCTX_DEFAULT)
_PORT_KEYS = frozenset({"name", "container_port"})


# -------------------------------------------------------------- 基础校验

def _int(value: Any, where: str, key: str, *, minimum: int,
         maximum: Optional[int] = None) -> int:
    """int 校验(不接受 bool——bool 是 int 子类,显式排除)。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(f"{where}.{key} must be an integer, got {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"({minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        raise InvalidParams(
            f"{where}.{key} must be an integer in {bound}, got {value!r}")
    return value


def _str(value: Any, where: str, key: str, *, max_len: int,
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


def _bool(value: Any, where: str, key: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidParams(f"{where}.{key} must be a boolean, got {value!r}")
    return value


def _caps(value: Any, where: str, key: str) -> list[str]:
    """capabilities 列表:非空 str 列表 + 排序去重(K8s 无序语义,
    排序消灭「下发顺序重排 → 指纹变化」的伪 A 类日落)。"""
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value):
        raise InvalidParams(
            f"{where}.{key} must be a list of non-empty strings, got {value!r}"
        )
    return sorted(set(value))


def _leading_slash(value: str) -> str:
    """路径补前导 '/'(缺它会拼出 "http://ip:8080api/..."——端口段粘连
    路径,httpx 直接抛非法端口 → 健康 Pod 被探死无限重部署)。"""
    return value if value.startswith("/") else f"/{value}"


# -------------------------------------------------------------- envFrom

def canonical_env_from(value: Any, where: str) -> Optional[list[dict[str, Any]]]:
    """envFrom → 内部规范形(secretRef/configMapRef 引用,值不落模板)。

    输入(内部 snake 形态;K8s wire 的 camelCase envFrom 由
    session_manager/container_spec.py 翻译后再进来):
    ``[{prefix?, secret_ref|config_map_ref: {name, optional?}}]``
    规范形:``[{prefix: str|None, <ref>: {name, optional}}]``;None/[] → None。

    canonical 以**全键**携带 ``env_from``(值可为 None)——2026-09 统一重构
    起不再用条件键(旧 sidecars.py 曾为存量指纹零扰动而条件化,指纹重置后
    该理由消失,条件键反而是「有值才出现」的等价异形来源)。
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


# -------------------------------------------------------------- 段落 canonical

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


def _canonical_ports(value: Any, where: str,
                     role: str) -> Optional[list[dict[str, Any]]]:
    """ports → 规范形;role 决定端口命名契约与缺省。

    main:恰一个 name="sse"(gateway 直连契约)+ 至多一个 name="http",
    固定序 [sse, http?];缺省 = [{sse, 8080}]。sidecar:至多一个**无名**
    端口(有名端口 K8s 端口名撞号类 bug,纯声明性不进 Service);缺省 None。
    """
    if value is None:
        return list(MAIN_PORTS_DEFAULT) if role == MAIN_ROLE else None
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where}.ports must be a list of port objects, got {value!r}")
    entries: list[dict[str, Any]] = []
    for i, entry in enumerate(value):
        entry_where = f"{where}.ports[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        unknown = set(entry) - _PORT_KEYS
        if unknown:
            raise InvalidParams(
                f"{entry_where} unknown keys {sorted(unknown)}; allowed: "
                f"{sorted(_PORT_KEYS)}")
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise InvalidParams(
                f"{entry_where}.name must be a string or null, got {name!r}")
        entries.append({"name": name, "container_port": _int(
            entry.get("container_port"), entry_where, "container_port",
            minimum=1, maximum=65535)})
    if role == MAIN_ROLE:
        others = [e for e in entries if e["name"] not in ("sse", "http")]
        http = [e for e in entries if e["name"] == "http"]
        sse = [e for e in entries if e["name"] == "sse"]
        if others:
            raise InvalidParams(
                f"{where}.ports supports only names 'sse' and 'http', "
                f"got {[e['name'] for e in others]!r}")
        if len(http) > 1:
            raise InvalidParams(
                f"{where}.ports allows at most one 'http' port, got {value!r}")
        if len(sse) != 1:
            raise InvalidParams(
                f"{where}.ports must contain exactly one port named 'sse' "
                f"(the gateway SSE contract), got {value!r}")
        return [sse[0]] + http
    if len(entries) > 1:
        raise InvalidParams(
            f"{where}.ports supports at most one port for a sidecar "
            f"container, got {value!r}")
    if entries and entries[0]["name"] is not None:
        raise InvalidParams(
            f"{where}.ports[0].name must be null for a sidecar container "
            f"(unnamed declarative port), got {entries[0]['name']!r}")
    return entries or None


def _canonical_resources(value: Any, where: str) -> dict[str, Optional[str]]:
    """resources → 嵌套规范形(四键恒满,str|None;与容器表段落同形)。"""
    if value is None:
        return dict(RESOURCES_DEFAULT)
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.resources must be an object, got {value!r}")
    unknown = set(value) - set(_RESOURCE_KEYS)
    if unknown:
        raise InvalidParams(
            f"{where}.resources unknown keys {sorted(unknown)}; allowed: "
            f"{list(_RESOURCE_KEYS)}")
    return {key: _str(value.get(key), where, key, max_len=32, required=False)
            for key in _RESOURCE_KEYS}


def _canonical_str_list(value: Any, where: str, key: str) -> Optional[list[str]]:
    """command/args → list[str] | None(None/[] 同义 = 走镜像入口;
    非列表或含非字符串项 → 400)。"""
    if value is None or value == []:
        return None
    if not isinstance(value, list):
        raise InvalidParams(f"{where}.{key} must be a list of strings, "
                            f"got {value!r}")
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise InvalidParams(
                f"{where}.{key}[{i}] must be a string, got {item!r}")
    return list(value)


def _canonical_secctx(value: Any, where: str,
                      role: str) -> dict[str, Any]:
    """securityContext → 7 键规范形;main 值域仅 runAs 两键(越角色 400)。"""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.security_context must be an object, got {value!r}")
    unknown = set(value) - _SECCTX_KEYS
    if unknown:
        raise InvalidParams(
            f"{where}.security_context unknown keys {sorted(unknown)}; "
            f"allowed: {sorted(_SECCTX_KEYS)}")
    out = dict(SECCTX_DEFAULT)
    for key in ("run_as_user", "run_as_group"):
        if value.get(key) is not None:
            out[key] = _int(value[key], f"{where}.security_context", key,
                            minimum=0)
    out["privileged"] = _bool(
        value.get("privileged", False), f"{where}.security_context",
        "privileged")
    for key in ("capabilities_add", "capabilities_drop"):
        if value.get(key) is not None:
            out[key] = _caps(value.get(key), f"{where}.security_context", key)
    for key in ("seccomp_unconfined", "apparmor_unconfined"):
        out[key] = _bool(
            value.get(key, False), f"{where}.security_context", key)
    if role == MAIN_ROLE:
        extras = {
            "privileged": out["privileged"],
            "capabilities_add": out["capabilities_add"],
            "capabilities_drop": out["capabilities_drop"],
            "seccomp_unconfined": out["seccomp_unconfined"],
            "apparmor_unconfined": out["apparmor_unconfined"],
        }
        if any(extras.values()):
            raise InvalidParams(
                f"{where}.security_context: only run_as_user/run_as_group are "
                "allowed on the main container (privileged/capabilities/"
                "seccomp/apparmor are sidecar-only)")
    return out


def _canonical_probe(value: Any, where: str, role: str) -> dict[str, Any]:
    """readinessProbe → 5 键规范形;role 决定缺省与值域。

    main:恒 http(RM 渲染不变式)、timeout 恒 None(= 不支持,非未设置;
    wire 侧 main timeoutSeconds → 400 在 container_spec 维持)。sidecar:
    tcp/http 二选一可缺省,timeout 默认 3。period 默认 main 5 / sidecar 10
    ——历史默认,不得拉平(拉平 = 全量模板指纹变化)。
    """
    default = (MAIN_PROBE_DEFAULT if role == MAIN_ROLE
               else SIDECAR_PROBE_DEFAULT)
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.readiness_probe must be an object, got {value!r}")
    unknown = set(value) - _PROBE_KEYS
    if unknown:
        raise InvalidParams(
            f"{where}.readiness_probe unknown keys {sorted(unknown)}; "
            f"allowed: {sorted(_PROBE_KEYS)}")
    out = dict(default)
    probe_type = value.get("probe_type", default["probe_type"])
    if probe_type is not None and probe_type not in _PROBE_TYPES:
        raise InvalidParams(
            f"{where}.readiness_probe.probe_type must be 'tcp' or 'http', "
            f"got {probe_type!r}")
    if role == MAIN_ROLE and probe_type != "http":
        raise InvalidParams(
            f"{where}.readiness_probe.probe_type must be 'http' on the main "
            "container (the gateway SSE contract renders an httpGet probe)")
    out["probe_type"] = probe_type
    if value.get("path") is not None:
        out["path"] = _leading_slash(_str(
            value["path"], f"{where}.readiness_probe", "path",
            max_len=_PROBE_PATH_MAX))
    if value.get("initial_delay") is not None:
        out["initial_delay"] = _int(
            value["initial_delay"], f"{where}.readiness_probe",
            "initial_delay", minimum=0)
    if value.get("period") is not None:
        out["period"] = _int(
            value["period"], f"{where}.readiness_probe", "period", minimum=1)
    if value.get("timeout") is not None:
        if role == MAIN_ROLE:
            raise InvalidParams(
                f"{where}.readiness_probe.timeout is not supported on the "
                "main container probe")
        out["timeout"] = _int(
            value["timeout"], f"{where}.readiness_probe", "timeout",
            minimum=1, maximum=300)
    return out


# -------------------------------------------------------------- 单容器 canonical

def canonical_container(item: Any, where: str, *, role: str) -> dict[str, Any]:
    """容器 dict → canonical 规范形(13 键全填满);非法 raise InvalidParams。

    幂等:canonical_container(canonical_container(x)) == canonical_container(x)。
    role 只影响值域与缺省,不影响键集。消息带 ``where`` 定位。
    """
    if role not in (MAIN_ROLE, SIDECAR_ROLE):
        raise ValueError(f"unknown role {role!r}")
    if not isinstance(item, dict):
        raise InvalidParams(f"{where} must be an object, got {item!r}")
    unknown = set(item) - _CONTAINER_KEYS
    if unknown:
        raise InvalidParams(
            f"{where} unknown keys {sorted(unknown)}; allowed: "
            f"{sorted(_CONTAINER_KEYS)}")
    name = item.get("name")
    if name is None and role == MAIN_ROLE:
        name = DEFAULT_MAIN_NAME  # 主容器缺省名(wire/container_spec 同源)
    if (not isinstance(name, str) or not name
            or not CONTAINER_NAME_RE.match(name)):
        raise InvalidParams(
            f"{where}.name {name!r} must be a DNS-1123 label (lowercase "
            "alphanumeric or '-'), max 63 chars")
    ports = _canonical_ports(item.get("ports"), where, role)
    probe = _canonical_probe(item.get("readiness_probe"), where, role)
    if (role == SIDECAR_ROLE and probe["probe_type"] is not None
            and ports is None):
        raise InvalidParams(
            f"{where}.readiness_probe.probe_type requires ports on a sidecar "
            "container (tcp/http probes target the container's own port)")
    return {
        "name": name,
        "image": _str(item.get("image"), where, "image",
                      max_len=_MAX_IMAGE_LEN),
        "image_pull_policy": _str(
            item.get("image_pull_policy") or DEFAULT_IMAGE_PULL_POLICY,
            where, "image_pull_policy", max_len=64),
        "command": _canonical_str_list(item.get("command"), where, "command"),
        "args": _canonical_str_list(item.get("args"), where, "args"),
        "ports": ports,
        "env": _canonical_env(item.get("env") or {}, where),
        "env_from": canonical_env_from(item.get("env_from"),
                                       f"{where}.env_from"),
        "resources": _canonical_resources(item.get("resources"), where),
        "host_path_mounts": canonical_host_path_mounts(
            item.get("host_path_mounts") or [], f"{where}.host_path_mounts"),
        "configmap_mounts": canonical_configmap_mounts(
            item.get("configmap_mounts") or [], f"{where}.configmap_mounts"),
        "pvc_mounts": canonical_pvc_mounts(
            item.get("pvc_mounts") or [], f"{where}.pvc_mounts"),
        "nfs_mounts": canonical_nfs_mounts(
            item.get("nfs_mounts") or [], f"{where}.nfs_mounts"),
        "security_context": _canonical_secctx(
            item.get("security_context"), where, role),
        "readiness_probe": probe,
    }


def default_main_container() -> dict[str, Any]:
    """Template 缺省主容器(空镜像哨兵——与旧 agent_image="" 同语义:
    未配置模板渲染空镜像,由上层拒绝/覆盖)。所有值取自默认值单源常量,
    不走 canonical(image 必填校验会拒空串)。"""
    return {
        "name": DEFAULT_MAIN_NAME,
        "image": "",
        "image_pull_policy": DEFAULT_IMAGE_PULL_POLICY,
        "command": None,
        "args": None,
        "ports": list(MAIN_PORTS_DEFAULT),
        "env": {},
        "env_from": None,
        "resources": dict(RESOURCES_DEFAULT),
        "host_path_mounts": [],
        "configmap_mounts": [],
        "pvc_mounts": [],
        "nfs_mounts": [],
        "security_context": dict(SECCTX_DEFAULT),
        "readiness_probe": dict(MAIN_PROBE_DEFAULT),
    }


# -------------------------------------------------------------- 宽容归一(读路径防御)

def normalize_container(value: Any, *, role: str) -> Optional[dict[str, Any]]:
    """宽容归一(不抛异常,读路径防御):

    - main:None/非 dict/无 image(空哨兵)→ default_main_container();
      其余坏值 WARNING + 回退默认(与旧 Template 扁平字段兜底同语义)。
    - sidecar:坏项返回 None(调用方丢弃,同旧 normalize_sidecars)。
    """
    where = "main_container" if role == MAIN_ROLE else "sidecars[n]"
    if not isinstance(value, dict):
        if role == MAIN_ROLE:
            return default_main_container()
        return None
    if role == MAIN_ROLE and not value.get("image"):
        return default_main_container()
    try:
        return canonical_container(value, where, role=role)
    except InvalidParams as exc:
        if role == MAIN_ROLE:
            logger.warning("invalid main container spec, fallback to "
                           "default: %s", exc)
            return default_main_container()
        return None


def normalize_containers(value: Any) -> Optional[list[dict[str, Any]]]:
    """sidecar 列表宽容归一:None/[]/非 list → None;坏项静默丢弃;
    合法项走规范形后按 name 升序(指纹对列表顺序稳定)。"""
    if not isinstance(value, list):
        return None
    items = [norm for item in value
             if (norm := normalize_container(item, role=SIDECAR_ROLE))
             is not None]
    return sorted(items, key=lambda sc: sc["name"]) or None


# -------------------------------------------------------------- 跨容器校验

def find_container_conflict(
        main: dict[str, Any],
        sidecars: list[dict[str, Any]],
) -> Optional[str]:
    """纯谓词:返回首个跨容器冲突描述(SM 包 InvalidParams、RM 包
    DeployFailed 共用)。

    - sidecar name == 主容器 name(K8s 同 Pod 容器名必须唯一)
    - sidecar port 撞主容器任一端口 / 兄弟 sidecar port(同 Pod 共享网络
      命名空间,agent 经 127.0.0.1:port 访问 sidecar,撞号几乎必然是配错
      ——有意的严格)
    """
    for i, sc in enumerate(sidecars):
        if sc["name"] == main["name"]:
            return (f"sidecars[{i}].name {sc['name']!r} conflicts with the "
                    f"main container name {main['name']!r}")
    main_ports = {p["container_port"] for p in main.get("ports") or []}
    seen: dict[int, str] = {}
    for i, sc in enumerate(sidecars):
        ports = [p["container_port"] for p in sc.get("ports") or []]
        for port in ports:
            if port in main_ports:
                return (f"sidecars[{i}].ports {port} conflicts with the main "
                        f"container ports {sorted(main_ports)}; sidecar ports "
                        "must differ from main container ports and each other")
            if port in seen:
                return (f"sidecars[{i}].ports {port} conflicts with "
                        f"{seen[port]}; sidecar ports must differ from each "
                        "other")
            seen[port] = f"sidecars[{i}].ports"
    return None


def validate_pod_containers(
        main: Any,
        sidecars: Any,
        where: str,
) -> tuple[dict[str, Any], Optional[list[dict[str, Any]]]]:
    """config_sync 下发校验(fail-fast 400):main/sidecars → canonical 对。

    跨容器:≤SIDECAR_MAX、sidecar 重名、容器名/端口冲突(委托
    find_container_conflict);容器内:三类挂载 mount_path 冲突(main 另含
    nfs.mount_path)。合法返回 (canonical main, canonical sidecars|None)。
    """
    canonical_main = canonical_container(main, f"{where}.main_container",
                                         role=MAIN_ROLE)
    # main 四类挂载 mount_path 互斥(K8s 会拒,这里 fail-fast)
    mount_conflict = find_mount_path_conflicts([
        (f"{where}.main_container.host_path_mounts",
         canonical_main["host_path_mounts"]),
        (f"{where}.main_container.configmap_mounts",
         canonical_main["configmap_mounts"]),
        (f"{where}.main_container.pvc_mounts",
         canonical_main["pvc_mounts"]),
        (f"{where}.main_container.nfs_mounts",
         canonical_main["nfs_mounts"]),
    ])
    if mount_conflict:
        raise InvalidParams(f"{where}.main_container: {mount_conflict}")
    if sidecars is None:
        return canonical_main, None
    if not isinstance(sidecars, list):
        raise InvalidParams(
            f"{where}.sidecars must be a list of container objects, "
            f"got {sidecars!r}")
    if len(sidecars) > SIDECAR_MAX:
        raise InvalidParams(
            f"{where}.sidecars must have at most {SIDECAR_MAX} entries, "
            f"got {len(sidecars)}")
    items = [canonical_container(sc, f"{where}.sidecars[{i}]", role=SIDECAR_ROLE)
             for i, sc in enumerate(sidecars)]
    # sidecar 探针需端口(tcp/http 都打自身端口)
    for i, sc in enumerate(items):
        if sc["readiness_probe"]["probe_type"] is not None and not sc["ports"]:
            raise InvalidParams(
                f"{where}.sidecars[{i}] requires ports when readiness_probe."
                "probe_type is set")
    names = [sc["name"] for sc in items]
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise InvalidParams(f"{where}.sidecars duplicate container names: "
                            f"{dupes}")
    conflict = find_container_conflict(canonical_main, items)
    if conflict:
        raise InvalidParams(f"{where}.sidecars: {conflict}")
    # 每个 sidecar 自身挂载的 mount_path 不得重复(K8s 会拒,这里 fail-fast)
    for i, sc in enumerate(items):
        mount_conflict = find_mount_path_conflicts([
            (f"{where}.sidecars[{i}].host_path_mounts",
             sc["host_path_mounts"]),
            (f"{where}.sidecars[{i}].configmap_mounts",
             sc["configmap_mounts"]),
            (f"{where}.sidecars[{i}].pvc_mounts", sc["pvc_mounts"]),
            (f"{where}.sidecars[{i}].nfs_mounts", sc["nfs_mounts"]),
        ])
        if mount_conflict:
            raise InvalidParams(f"{where}.sidecars[{i}]: {mount_conflict}")
    return canonical_main, sorted(items, key=lambda sc: sc["name"]) or None


# -------------------------------------------------------------- 派生 helper(RM/SM 读取单源)

def main_sse_port(cont: Optional[dict[str, Any]]) -> int:
    """主容器 sse 端口(gateway 直连契约);缺省/坏值兜底 8080。"""
    for port in (cont or {}).get("ports") or []:
        if port.get("name") == "sse":
            return int(port["container_port"])
    return DEFAULT_SSE_PORT


def main_health_path(cont: Optional[dict[str, Any]]) -> str:
    """主容器健康路径(readiness 同源);缺省/坏值兜底 /health。"""
    path = (cont or {}).get("readiness_probe", {}).get("path")
    return path if isinstance(path, str) and path else "/health"


# -------------------------------------------------------------- pod_spec 正规化

_POD_SPEC_CONTAINER_KEYS = ("main_container", "sidecars")


def normalize_pod_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """pod_spec 指纹/渲染前置正规化:模板级字段透传,容器段**只补 canonical
    缺省键、绝不改已有值、不丢项**。

    为什么承重:deploy_ver 是跨版本派生契约,未来加容器字段(新键默认值 ==
    旧行为)时,Redis 里未刷新的旧 pod_spec_json 经此正规化后与新算指纹相等
    → 零伪 A 类日落;行为性新键(默认值 ≠ 旧行为)应当日落,日落正确。
    与 canonical_container 的分工:那边 fail-fast 拒坏值(写路径),这边
    best-effort 补缺(读路径,老缓存友好)。
    """
    out = dict(spec)
    main = spec.get("main_container")
    if isinstance(main, dict):
        out["main_container"] = _fill_defaults(main, MAIN_ROLE)
    sidecars = spec.get("sidecars")
    if isinstance(sidecars, list):
        filled = [f for sc in sidecars
                  if isinstance(sc, dict)
                  and (f := _fill_defaults(sc, SIDECAR_ROLE)) is not None]
        # 列表序非语义(canonical 序 = name 升序),指纹边界对齐排序
        out["sidecars"] = sorted(filled, key=lambda sc: sc["name"])
    return out


def _fill_defaults(item: dict[str, Any], role: str) -> Optional[dict[str, Any]]:
    """单容器补缺省(不改已有值;sidecar 缺 name/image 的坏项返回 None)。

    段落级(sub-dict/list)缺整个键时补默认骨架;已有段落内缺键时同样逐键
    补齐(nfs/probe/secctx/resources 嵌套默认)。不复制未知键(保持指纹对
    未知键可见——静默吞键会造伪相等)。
    """
    if role == SIDECAR_ROLE and (
            not isinstance(item.get("name"), str) or not item.get("name")
            or not isinstance(item.get("image"), str) or not item.get("image")):
        return None
    out = dict(item)
    if not isinstance(out.get("image_pull_policy"), str) \
            or not out["image_pull_policy"]:
        out["image_pull_policy"] = DEFAULT_IMAGE_PULL_POLICY
    for key in ("command", "args"):
        if key not in out or out[key] == []:
            out[key] = None
    if not isinstance(out.get("ports"), list):
        out["ports"] = (list(MAIN_PORTS_DEFAULT) if role == MAIN_ROLE
                        else None)
    if not isinstance(out.get("env"), dict):
        out["env"] = {}
    if "env_from" not in out or out["env_from"] == []:
        out["env_from"] = None
    resources = out.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    out["resources"] = {
        key: resources.get(key) for key in _RESOURCE_KEYS}
    for key in ("host_path_mounts", "configmap_mounts", "pvc_mounts",
                "nfs_mounts"):
        if not isinstance(out.get(key), list):
            out[key] = []
    secctx = out.get("security_context")
    if not isinstance(secctx, dict):
        secctx = {}
    out["security_context"] = {
        key: secctx.get(key, default)
        for key, default in SECCTX_DEFAULT.items()}
    probe = out.get("readiness_probe")
    if not isinstance(probe, dict):
        probe = {}
    probe_default = (MAIN_PROBE_DEFAULT if role == MAIN_ROLE
                     else SIDECAR_PROBE_DEFAULT)
    out["readiness_probe"] = {
        key: probe.get(key, default)
        for key, default in probe_default.items()}
    return out
