# coding: utf-8
"""容器规范形层 —— K8s 原生 wire ↔ 内部 Template/sidecar 规范形翻译(SM 私有)。

- 表 ``service_config_container``:容器规格系统记录(一容器一行,主/sidecar 同表,
  角色由模板引用位置决定——main_container_id / sidecar_container_ids)。
- config_sync 三段式契约的 ``containers`` 段解析:wire 用 K8s API 拼写
  (camelCase:imagePullPolicy/containerPort/mountPath/periodSeconds…),业务键
  ``container_id`` 保持本仓 snake_case;解析产物 = 内部规范形(snake 键,
  即 DB JSON 列的存储形态)。
- 卷采用 K8s Pod 规范同构:**模板级 volumes 定义 + 容器级 volumeMounts 按名引用**;
  水合时 join 重建内部 fused 挂载形态(mounts.py 规范形,指纹承重)。
- 指纹红线:sidecar 投影复用 ``sidecars.py`` 既有规范形输出(24 键 + env_from
  条件键),主容器投影逐项对齐 ``Template`` 默认——同值必同 deploy_ver。

与 ``sidecars.py``/``mounts.py`` 的分工:那两个是 SM/RM 共享的**内部形态**校验层
(输出冻结);本模块是 SM 私有的 wire 翻译层,产物再交给它们规范化。RM 不感知
容器表(deploy_subset 仍是扁平 Template 字段)。
"""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    TableDefinition,
)

from ..errors import InvalidParams
from ..mounts import (
    canonical_configmap_mounts,
    canonical_host_path_mounts,
    canonical_pvc_mounts,
    validate_agent_mounts,
)
from ..sidecars import (
    SIDECAR_NAME_RE,
    canonical_env_from,
)

CONTAINER_TABLE = "service_config_container"

CONTAINER_ID_MAX = 100
MAIN_ROLE = "main"
SIDECAR_ROLE = "sidecar"

# 容器表:标量列 + 段落 JSON 列(内容为本模块产出的内部规范形,snake 键)。
# 新表由框架 init_table 自动建(create_all),无需手工 DDL。
SERVICE_CONFIG_CONTAINER_TABLE_DEF = TableDefinition(
    table_name=CONTAINER_TABLE,
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False,
                         default=""),
        ColumnDefinition("container_id", "string", length=100, nullable=False,
                         unique=True),
        ColumnDefinition("name", "string", length=128, nullable=False),
        ColumnDefinition("image", "string", length=512, nullable=False),
        ColumnDefinition("image_pull_policy", "string", length=64,
                         nullable=False, default="IfNotPresent"),
        ColumnDefinition("ports", "json", nullable=True),
        ColumnDefinition("env", "json", nullable=True),
        ColumnDefinition("env_from", "json", nullable=True),
        ColumnDefinition("resources", "json", nullable=True),
        ColumnDefinition("volume_mounts", "json", nullable=True),
        ColumnDefinition("security_context", "json", nullable=True),
        ColumnDefinition("readiness_probe", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
)

# wire 合法键(K8s V1Container 子集 + 业务键 container_id;command/args 等
# 内部表达不了的字段不在白名单 → 出现即 400,绝不静默丢弃)
_CONTAINER_WIRE_KEYS = frozenset({
    "container_id", "name", "image", "imagePullPolicy", "ports", "env",
    "envFrom", "resources", "volumeMounts", "securityContext",
    "readinessProbe",
})

_PORT_ENTRY_KEYS = frozenset({"name", "containerPort"})
_ENV_ENTRY_KEYS = frozenset({"name", "value"})
_MOUNT_ENTRY_KEYS = frozenset({"name", "mountPath", "subPath", "readOnly"})
_RESOURCE_KEYS = frozenset({"requests", "limits"})
_RESOURCE_ITEM_KEYS = frozenset({"cpu", "memory"})
_PROBE_KEYS = frozenset(
    {"httpGet", "tcpSocket", "initialDelaySeconds", "periodSeconds",
     "timeoutSeconds"})
_HTTP_GET_KEYS = frozenset({"path", "port"})
_TCP_SOCKET_KEYS = frozenset({"port"})
_MAIN_SECCTX_KEYS = frozenset({"runAsUser", "runAsGroup"})
_SIDECAR_SECCTX_EXTRA = {
    "privileged", "capabilities", "seccompProfile", "appArmorProfile",
}
_SECCTX_PROFILE_TYPES = {"Unconfined": True, "RuntimeDefault": False}

# 模板级 volumes:K8s 卷源键 → 内部 kind
_VOLUME_WIRE_SOURCES = ("hostPath", "configMap", "persistentVolumeClaim", "nfs")


def _int(value: Any, where: str, key: str, *, minimum: int,
         maximum: Optional[int] = None) -> int:
    """int 校验(不接受 bool;同 sidecars._canonical_int 语义)。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(f"{where}.{key} must be an integer, got {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"({minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        raise InvalidParams(
            f"{where}.{key} must be an integer in {bound}, got {value!r}")
    return value


def _nonempty_str(value: Any, where: str, key: str, *, max_len: int) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > max_len):
        raise InvalidParams(
            f"{where}.{key} must be a non-empty string of at most "
            f"{max_len} chars, got {value!r}")
    return value


# -------------------------------------------------------------- 段落解析

def _parse_ports(value: Any, where: str, role: str) -> Optional[list[dict]]:
    """ports → 内部规范形 [{name: str|None, container_port: int}]。

    主容器:必须有 name="sse" 端口(gateway 直连契约),可另有一个 name="http";
    缺省整体 = [{sse, 8080}](Template.sse_port/container_port 默认)。
    sidecar:至多 1 个无名端口(有名端口内部表达不了 → 400)。
    """
    if value is None:
        if role == MAIN_ROLE:
            return [{"name": "sse", "container_port": 8080}]
        return None
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where}.ports must be a list of port objects, got {value!r}")
    entries: list[dict] = []
    for i, entry in enumerate(value):
        entry_where = f"{where}.ports[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        unknown = set(entry) - _PORT_ENTRY_KEYS
        if unknown:
            raise InvalidParams(
                f"{entry_where} unknown keys {sorted(unknown)}; allowed: "
                f"{sorted(_PORT_ENTRY_KEYS)}")
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise InvalidParams(
                f"{entry_where}.name must be a string or null, got {name!r}")
        port = _int(entry.get("containerPort"), entry_where, "containerPort",
                    minimum=1, maximum=65535)
        entries.append({"name": name, "container_port": port})
    if role == MAIN_ROLE:
        others = [e for e in entries if e["name"] not in ("sse", "http")]
        http = [e for e in entries if e["name"] == "http"]
        sse = [e for e in entries if e["name"] == "sse"]
        if others:
            raise InvalidParams(
                f"{where}.ports supports only names 'sse' and 'http', "
                f"got {[e['name'] for e in entries]!r}")
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


def _parse_env(value: Any, where: str) -> dict[str, str]:
    """env(K8s 列表 [{name, value}])→ 内部 dict(与 agent_env/侧 env 同形)。"""
    if value is None:
        return {}
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where}.env must be a list of {{name, value}} objects, "
            f"got {value!r}")
    out: dict[str, str] = {}
    for i, entry in enumerate(value):
        entry_where = f"{where}.env[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        unknown = set(entry) - _ENV_ENTRY_KEYS
        if unknown:
            raise InvalidParams(
                f"{entry_where} unknown keys {sorted(unknown)}; allowed: "
                f"{sorted(_ENV_ENTRY_KEYS)}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidParams(
                f"{entry_where}.name must be a non-empty string, got {name!r}")
        entry_value = entry.get("value")
        if not isinstance(entry_value, str):
            raise InvalidParams(
                f"{entry_where}.value must be a string, got {entry_value!r}")
        if name in out:
            raise InvalidParams(
                f"{entry_where}.name {name!r} duplicates an earlier env entry")
        out[name] = entry_value
    return out


def _parse_env_from(value: Any, where: str) -> Optional[list[dict]]:
    """wire envFrom(camel)→ 内部规范形(canonical_env_from 校验)。"""
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where}.envFrom must be a list of envFrom sources, got {value!r}")
    translated: list[dict] = []
    for i, entry in enumerate(value):
        entry_where = f"{where}.envFrom[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        unknown = set(entry) - {"prefix", "secretRef", "configMapRef"}
        if unknown:
            raise InvalidParams(
                f"{entry_where} unknown keys {sorted(unknown)}; allowed: "
                "['configMapRef', 'prefix', 'secretRef']")
        item: dict[str, Any] = {}
        if "prefix" in entry:
            item["prefix"] = entry["prefix"]
        for wire_key, internal_key in (("secretRef", "secret_ref"),
                                       ("configMapRef", "config_map_ref")):
            if wire_key in entry:
                ref = entry[wire_key]
                if not isinstance(ref, dict):
                    raise InvalidParams(
                        f"{entry_where}.{wire_key} must be an object, "
                        f"got {ref!r}")
                ref_unknown = set(ref) - {"name", "optional"}
                if ref_unknown:
                    raise InvalidParams(
                        f"{entry_where}.{wire_key} unknown keys "
                        f"{sorted(ref_unknown)}; allowed: ['name', 'optional']")
                item[internal_key] = ref
        translated.append(item)
    return canonical_env_from(translated, f"{where}.envFrom")


def _parse_resources(value: Any, where: str) -> dict[str, Optional[str]]:
    """resources(K8s 嵌套)→ 内部扁平四字段(与 Template/sidecar 同名)。"""
    out: dict[str, Optional[str]] = {
        "cpu_request": None, "memory_request": None,
        "cpu_limit": None, "memory_limit": None,
    }
    if value is None:
        return out
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.resources must be an object, got {value!r}")
    unknown = set(value) - _RESOURCE_KEYS
    if unknown:
        raise InvalidParams(
            f"{where}.resources unknown keys {sorted(unknown)}; allowed: "
            f"{sorted(_RESOURCE_KEYS)}")
    for section, suffix in (("requests", "request"), ("limits", "limit")):
        entries = value.get(section)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise InvalidParams(
                f"{where}.resources.{section} must be an object, "
                f"got {entries!r}")
        section_unknown = set(entries) - _RESOURCE_ITEM_KEYS
        if section_unknown:
            raise InvalidParams(
                f"{where}.resources.{section} unknown keys "
                f"{sorted(section_unknown)}; allowed: ['cpu', 'memory']")
        for resource, quantity in entries.items():
            out[f"{resource}_{suffix}"] = _nonempty_str(
                quantity, f"{where}.resources.{section}", resource, max_len=32)
    return out


def _parse_volume_mounts(value: Any, where: str) -> list[dict]:
    """volumeMounts → 内部 [{name, mount_path, sub_path, read_only}]。

    read_only 为 None 表示 wire 未给——fuse 时按卷源类型落定内部规范默认
    (configMap→true、hostPath/PVC→false,mounts.py 指纹承重默认)。
    绝对/相对路径与资源名的完整校验由 fuse 侧 canonical 函数兜(幂等)。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidParams(
            f"{where}.volumeMounts must be a list, got {value!r}")
    out: list[dict] = []
    for i, entry in enumerate(value):
        entry_where = f"{where}.volumeMounts[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        unknown = set(entry) - _MOUNT_ENTRY_KEYS
        if unknown:
            raise InvalidParams(
                f"{entry_where} unknown keys {sorted(unknown)}; allowed: "
                f"{sorted(_MOUNT_ENTRY_KEYS)}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidParams(
                f"{entry_where}.name must be a non-empty string, got {name!r}")
        mount_path = entry.get("mountPath")
        if not isinstance(mount_path, str) or not mount_path:
            raise InvalidParams(
                f"{entry_where}.mountPath must be a non-empty string, "
                f"got {mount_path!r}")
        sub_path = entry.get("subPath")
        if sub_path is not None and not isinstance(sub_path, str):
            raise InvalidParams(
                f"{entry_where}.subPath must be a string or null, "
                f"got {sub_path!r}")
        read_only = entry.get("readOnly")
        if read_only is not None and not isinstance(read_only, bool):
            raise InvalidParams(
                f"{entry_where}.readOnly must be a boolean or null, "
                f"got {read_only!r}")
        out.append({"name": name, "mount_path": mount_path,
                    "sub_path": sub_path, "read_only": read_only})
    return out


def _parse_security_context(value: Any, where: str,
                            role: str) -> dict[str, Any]:
    """securityContext → 内部八键规范形(主容器仅 runAs 两键合法,越角色 400)。"""
    out: dict[str, Any] = {
        "run_as_user": None, "run_as_group": None,
        "privileged": False, "capabilities_add": [], "capabilities_drop": [],
        "seccomp_unconfined": False, "apparmor_unconfined": False,
    }
    if value is None:
        return out
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.securityContext must be an object, got {value!r}")
    allowed = _MAIN_SECCTX_KEYS | (
        _SIDECAR_SECCTX_EXTRA if role == SIDECAR_ROLE else set())
    unknown = set(value) - allowed
    if unknown:
        role_note = ("only runAsUser/runAsGroup are allowed on the main "
                     "container" if role == MAIN_ROLE else "")
        raise InvalidParams(
            f"{where}.securityContext unknown keys {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
            + (f" ({role_note})" if role_note else ""))
    for wire_key, out_key in (("runAsUser", "run_as_user"),
                              ("runAsGroup", "run_as_group")):
        if value.get(wire_key) is not None:
            out[out_key] = _int(value[wire_key], where, wire_key, minimum=0)
    if value.get("privileged") is not None:
        privileged = value["privileged"]
        if not isinstance(privileged, bool):
            raise InvalidParams(
                f"{where}.securityContext.privileged must be a boolean, "
                f"got {privileged!r}")
        out["privileged"] = privileged
    capabilities = value.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            raise InvalidParams(
                f"{where}.securityContext.capabilities must be an object, "
                f"got {capabilities!r}")
        caps_unknown = set(capabilities) - {"add", "drop"}
        if caps_unknown:
            raise InvalidParams(
                f"{where}.securityContext.capabilities unknown keys "
                f"{sorted(caps_unknown)}; allowed: ['add', 'drop']")
        for section, out_key in (("add", "capabilities_add"),
                                 ("drop", "capabilities_drop")):
            caps = capabilities.get(section) or []
            if not isinstance(caps, list) or any(
                    not isinstance(c, str) or not c for c in caps):
                raise InvalidParams(
                    f"{where}.securityContext.capabilities.{section} must be "
                    f"a list of non-empty strings, got {caps!r}")
            out[out_key] = list(caps)
    for wire_key, out_key in (("seccompProfile", "seccomp_unconfined"),
                              ("appArmorProfile", "apparmor_unconfined")):
        profile = value.get(wire_key)
        if profile is None:
            continue
        if not isinstance(profile, dict) or set(profile) != {"type"}:
            raise InvalidParams(
                f"{where}.securityContext.{wire_key} must be an object with "
                f"exactly key 'type', got {profile!r}")
        profile_type = profile["type"]
        if profile_type not in _SECCTX_PROFILE_TYPES:
            raise InvalidParams(
                f"{where}.securityContext.{wire_key}.type must be one of "
                f"{sorted(_SECCTX_PROFILE_TYPES)}, got {profile_type!r}")
        out[out_key] = _SECCTX_PROFILE_TYPES[profile_type]
    return out


def _parse_readiness_probe(value: Any, where: str, role: str,
                           ports: Optional[list[dict]]) -> dict[str, Any]:
    """readinessProbe → 内部 {probe_type, path, initial_delay, period, timeout}。

    主容器:恒 httpGet(RM 渲染不变式);timeoutSeconds 内部无此项 → 400;
    缺省 {http, /health, 5, 5}(Template 默认)。sidecar:tcp/http 二选一可缺省,
    缺省 {None, /health, 5, 10, 3}(与 _canonical_sidecar 默认逐项相等)。
    探针 port 若给必须等于容器端口(主容器 = sse 端口)。
    """
    if role == MAIN_ROLE:
        out = {"probe_type": "http", "path": "/health",
               "initial_delay": 5, "period": 5, "timeout": None}
    else:
        out = {"probe_type": None, "path": "/health",
               "initial_delay": 5, "period": 10, "timeout": 3}
    if value is None:
        return out
    if not isinstance(value, dict):
        raise InvalidParams(
            f"{where}.readinessProbe must be an object, got {value!r}")
    unknown = set(value) - _PROBE_KEYS
    if unknown:
        raise InvalidParams(
            f"{where}.readinessProbe unknown keys {sorted(unknown)}; "
            f"allowed: {sorted(_PROBE_KEYS)}")
    has_http = "httpGet" in value
    has_tcp = "tcpSocket" in value
    if has_http and has_tcp:
        raise InvalidParams(
            f"{where}.readinessProbe: httpGet and tcpSocket are mutually "
            "exclusive")
    if role == MAIN_ROLE and has_tcp:
        raise InvalidParams(
            f"{where}.readinessProbe: the main container probe is always "
            "httpGet (tcpSocket is not supported)")
    if has_http or has_tcp:
        handler = value["httpGet" if has_http else "tcpSocket"]
        allowed = _HTTP_GET_KEYS if has_http else _TCP_SOCKET_KEYS
        if not isinstance(handler, dict):
            raise InvalidParams(
                f"{where}.readinessProbe.{'httpGet' if has_http else 'tcpSocket'} "
                f"must be an object, got {handler!r}")
        handler_unknown = set(handler) - allowed
        if handler_unknown:
            raise InvalidParams(
                f"{where}.readinessProbe.{'httpGet' if has_http else 'tcpSocket'} "
                f"unknown keys {sorted(handler_unknown)}; allowed: "
                f"{sorted(allowed)}")
        out["probe_type"] = "http" if has_http else "tcp"
        if has_http and handler.get("path") is not None:
            out["path"] = _nonempty_str(
                handler["path"], f"{where}.readinessProbe.httpGet", "path",
                max_len=128)
        own_port = ports[0]["container_port"] if ports else None
        if handler.get("port") is not None:
            probe_port = _int(handler["port"],
                              f"{where}.readinessProbe."
                              f"{'httpGet' if has_http else 'tcpSocket'}",
                              "port", minimum=1, maximum=65535)
            if probe_port != own_port:
                raise InvalidParams(
                    f"{where}.readinessProbe port {probe_port} must equal the "
                    f"container port {own_port} (probe port is derived, not "
                    "stored)")
    if value.get("initialDelaySeconds") is not None:
        out["initial_delay"] = _int(value["initialDelaySeconds"], where,
                                    "initialDelaySeconds", minimum=0)
    if value.get("periodSeconds") is not None:
        out["period"] = _int(value["periodSeconds"], where, "periodSeconds",
                             minimum=1)
    if value.get("timeoutSeconds") is not None:
        if role == MAIN_ROLE:
            raise InvalidParams(
                f"{where}.readinessProbe.timeoutSeconds is not supported on "
                "the main container probe")
        out["timeout"] = _int(value["timeoutSeconds"], where, "timeoutSeconds",
                              minimum=1, maximum=300)
    return out


# -------------------------------------------------------------- 容器 wire → 内部规范形

def parse_container_spec(item: Any, where: str, *, role: str) -> dict[str, Any]:
    """container wire dict(K8s camelCase + container_id)→ 内部规范形。

    role 决定键白名单与默认值(见各 _parse_* docstring);非法 raise
    InvalidParams(fail-fast,防配置静默丢失)。产物 = DB 行的段落形态。
    """
    if role not in (MAIN_ROLE, SIDECAR_ROLE):
        raise ValueError(f"unknown role {role!r}")
    if not isinstance(item, dict):
        raise InvalidParams(f"{where} must be an object, got {item!r}")
    unknown = set(item) - _CONTAINER_WIRE_KEYS
    if unknown:
        raise InvalidParams(
            f"{where} unknown keys {sorted(unknown)}; allowed: "
            f"{sorted(_CONTAINER_WIRE_KEYS)}")

    container_id = item.get("container_id")
    if (not isinstance(container_id, str) or not container_id.strip()
            or len(container_id) > CONTAINER_ID_MAX):
        raise InvalidParams(
            f"{where}.container_id must be a non-empty string of at most "
            f"{CONTAINER_ID_MAX} chars, got {container_id!r}")
    name = item.get("name")
    if role == MAIN_ROLE and name is None:
        name = "agent"  # Template.container_name 默认
    if not isinstance(name, str) or not SIDECAR_NAME_RE.match(name):
        raise InvalidParams(
            f"{where}.name {name!r} must be a DNS-1123 label (lowercase "
            "alphanumeric or '-'), max 63 chars")
    image = _nonempty_str(item.get("image"), where, "image", max_len=512)
    image_pull_policy = item.get("imagePullPolicy") or "IfNotPresent"
    if not isinstance(image_pull_policy, str) or not image_pull_policy.strip():
        raise InvalidParams(
            f"{where}.imagePullPolicy must be a non-empty string, "
            f"got {image_pull_policy!r}")
    ports = _parse_ports(item.get("ports"), where, role)
    env = _parse_env(item.get("env"), where)
    env_from = _parse_env_from(item.get("envFrom"), where)
    resources = _parse_resources(item.get("resources"), where)
    volume_mounts = _parse_volume_mounts(item.get("volumeMounts"), where)
    security_context = _parse_security_context(
        item.get("securityContext"), where, role)
    readiness_probe = _parse_readiness_probe(
        item.get("readinessProbe"), where, role, ports)
    return {
        "container_id": container_id,
        "name": name,
        "image": image,
        "image_pull_policy": image_pull_policy,
        "ports": ports,
        "env": env,
        "env_from": env_from,
        "resources": resources,
        "volume_mounts": volume_mounts,
        "security_context": security_context,
        "readiness_probe": readiness_probe,
    }


# -------------------------------------------------------------- 模板级 volumes

def canonical_volumes(value: Any, where: str) -> dict[str, dict[str, Any]]:
    """模板级 volumes wire → {卷名: 内部卷定义}(K8s spec.volumes 同构)。

    内部形态:{name, kind, …源字段};路径/资源名/items 的完整校验由
    fuse 侧 canonical_*_mounts 兜(每个卷必须被挂载才会进 fused,故
    未挂载卷在 config_store 模板级先拒)。
    """
    if value is None:
        return {}
    if not isinstance(value, list):
        raise InvalidParams(f"{where} must be a list of volume objects, "
                            f"got {value!r}")
    out: dict[str, dict[str, Any]] = {}
    for i, entry in enumerate(value):
        entry_where = f"{where}[{i}]"
        if not isinstance(entry, dict):
            raise InvalidParams(
                f"{entry_where} must be an object, got {entry!r}")
        sources = [k for k in _VOLUME_WIRE_SOURCES if k in entry]
        if len(sources) != 1:
            raise InvalidParams(
                f"{entry_where} must have exactly one volume source among "
                f"{list(_VOLUME_WIRE_SOURCES)}, got {sorted(entry)!r}")
        name = entry.get("name")
        if not isinstance(name, str) or not SIDECAR_NAME_RE.match(name):
            raise InvalidParams(
                f"{entry_where}.name {name!r} must be a DNS-1123 label, "
                "max 63 chars")
        if name in out:
            raise InvalidParams(
                f"{entry_where}.name {name!r} duplicates an earlier volume")
        source_key = sources[0]
        source = entry[source_key]
        if not isinstance(source, dict):
            raise InvalidParams(
                f"{entry_where}.{source_key} must be an object, "
                f"got {source!r}")
        if source_key == "hostPath":
            volume = {"kind": "host_path",
                      "host_path": _nonempty_str(
                          source.get("path"), f"{entry_where}.hostPath",
                          "path", max_len=256)}
            host_path_type = source.get("type")
            if host_path_type is not None and not isinstance(host_path_type, str):
                raise InvalidParams(
                    f"{entry_where}.hostPath.type must be a string or null, "
                    f"got {host_path_type!r}")
            volume["host_path_type"] = host_path_type
        elif source_key == "configMap":
            volume = {"kind": "configmap",
                      "config_map_name": _nonempty_str(
                          source.get("name"), f"{entry_where}.configMap",
                          "name", max_len=253)}
            items = source.get("items")
            if items is not None:
                if not isinstance(items, list):
                    raise InvalidParams(
                        f"{entry_where}.configMap.items must be a list of "
                        f"{{key, path}} objects, got {items!r}")
                volume["items"] = list(items)  # 键集校验由 fuse canonical 兜
            else:
                volume["items"] = None
        elif source_key == "persistentVolumeClaim":
            volume = {"kind": "pvc",
                      "claim_name": _nonempty_str(
                          source.get("claimName"),
                          f"{entry_where}.persistentVolumeClaim", "claimName",
                          max_len=253)}
        else:  # nfs
            volume = {"kind": "nfs",
                      "nfs_server": _nonempty_str(
                          source.get("server"), f"{entry_where}.nfs",
                          "server", max_len=256)}
            nfs_path = source.get("path")
            if nfs_path is not None and (
                    not isinstance(nfs_path, str) or not nfs_path):
                raise InvalidParams(
                    f"{entry_where}.nfs.path must be a non-empty string or "
                    f"null, got {nfs_path!r}")
            volume["nfs_path"] = nfs_path
        volume["name"] = name
        out[name] = volume
    return out


# -------------------------------------------------------------- join:fused 挂载重建

def mounted_volume_names(spec: dict[str, Any]) -> set[str]:
    """容器 volumeMounts 引用的卷名集合(模板级未挂载卷检查用)。"""
    return {m["name"] for m in spec.get("volume_mounts") or []}


def fuse_mounts(spec: dict[str, Any], volumes: dict[str, dict[str, Any]],
                where: str, role: str) -> dict[str, Any]:
    """volumeMounts × volumes join → 原始 fused 挂载 + NFS 三元组。

    返回 {host_path_mounts, configmap_mounts, pvc_mounts, nfs};
    前三者是 mounts.py 规范形函数的**输入**(raw 条目,调用方决定过
    canonical_* 还是直接交 validate_*);nfs = {server, path, mount_path} | None。
    源类型相关规则在此落定:subPath 仅 configMap、readOnly 默认按源类型
    (cm→true、hp/pvc→false)、NFS 只许主容器一个且不支持 readOnly。
    """
    host: list[dict] = []
    cm: list[dict] = []
    pvc: list[dict] = []
    nfs: Optional[dict[str, Any]] = None
    for i, mount in enumerate(spec.get("volume_mounts") or []):
        mount_where = f"{where}.volumeMounts[{i}]"
        volume = volumes.get(mount["name"])
        if volume is None:
            raise InvalidParams(
                f"{mount_where}.name {mount['name']!r} is not defined in "
                "the template volumes")
        kind = volume["kind"]
        read_only = mount["read_only"]
        if mount["sub_path"] is not None and kind != "configmap":
            raise InvalidParams(
                f"{mount_where}.subPath is only supported on configMap "
                f"volume mounts, not {kind}")
        if kind == "host_path":
            host.append({"host_path": volume["host_path"],
                         "mount_path": mount["mount_path"],
                         "read_only": False if read_only is None else read_only,
                         "host_path_type": volume["host_path_type"]})
        elif kind == "configmap":
            cm.append({"config_map_name": volume["config_map_name"],
                       "mount_path": mount["mount_path"],
                       "sub_path": mount["sub_path"],
                       "items": volume["items"],
                       "read_only": True if read_only is None else read_only})
        elif kind == "pvc":
            pvc.append({"claim_name": volume["claim_name"],
                        "mount_path": mount["mount_path"],
                        "read_only": False if read_only is None else read_only})
        else:  # nfs
            if role == SIDECAR_ROLE:
                raise InvalidParams(
                    f"{mount_where}: nfs volume {mount['name']!r} cannot be "
                    "mounted by a sidecar container")
            if read_only:
                raise InvalidParams(
                    f"{mount_where}: nfs volume mounts do not support "
                    "readOnly=true")
            if nfs is not None:
                raise InvalidParams(
                    f"{mount_where}: at most one nfs volume mount is allowed "
                    "per template")
            nfs = {"server": volume["nfs_server"], "path": volume["nfs_path"],
                   "mount_path": mount["mount_path"]}
    return {"host_path_mounts": host, "configmap_mounts": cm,
            "pvc_mounts": pvc, "nfs": nfs}


# -------------------------------------------------------------- 投影:内部规范形 → Template/sidecar

def _sse_port(spec: dict[str, Any]) -> int:
    return spec["ports"][0]["container_port"]


def _http_port(spec: dict[str, Any]) -> int:
    """主容器 http 端口;无则 = sse 端口(RM 渲染同名端口的既有约定)。"""
    if len(spec["ports"]) > 1:
        return spec["ports"][1]["container_port"]
    return spec["ports"][0]["container_port"]


def main_template_kwargs(spec: dict[str, Any],
                         volumes: dict[str, dict[str, Any]],
                         where: str) -> dict[str, Any]:
    """主容器内部规范形(+模板 volumes join)→ Template 容器级 kwargs。

    与 Template 默认逐项对齐(缺省落定不漂指纹);挂载经
    validate_agent_mounts 规范化 + 冲突检查(含撞 nfs_mount_path)。
    """
    fused = fuse_mounts(spec, volumes, where, MAIN_ROLE)
    (host, cm, pvc) = validate_agent_mounts(
        fused["host_path_mounts"] or None,
        fused["configmap_mounts"] or None,
        fused["pvc_mounts"] or None,
        nfs_mount_path=fused["nfs"]["mount_path"] if fused["nfs"] else None,
    )
    secctx = spec["security_context"]
    probe = spec["readiness_probe"]
    resources = spec["resources"]
    return {
        "container_name": spec["name"],
        "agent_image": spec["image"],
        "image_pull_policy": spec["image_pull_policy"],
        "sse_port": _sse_port(spec),
        "container_port": _http_port(spec),
        "agent_env": spec["env"],
        "agent_env_from": spec["env_from"],
        "agent_cpu_request": resources["cpu_request"],
        "agent_memory_request": resources["memory_request"],
        "agent_cpu_limit": resources["cpu_limit"],
        "agent_memory_limit": resources["memory_limit"],
        "run_as_user": secctx["run_as_user"],
        "run_as_group": secctx["run_as_group"],
        "health_path": probe["path"],
        "readiness_initial_delay": probe["initial_delay"],
        "readiness_period": probe["period"],
        "agent_host_path_mounts": host,
        "agent_configmap_mounts": cm,
        "agent_pvc_mounts": pvc,
        "nfs_server": fused["nfs"]["server"] if fused["nfs"] else None,
        "nfs_path": fused["nfs"]["path"] if fused["nfs"] else None,
        "nfs_mount_path": fused["nfs"]["mount_path"] if fused["nfs"] else None,
    }


def sidecar_wire_input(spec: dict[str, Any],
                       volumes: dict[str, dict[str, Any]],
                       where: str) -> dict[str, Any]:
    """sidecar 内部规范形(+模板 volumes join)→ sidecars.py 校验输入形态。

    产物交 validate_sidecars(幂等再规范化 + ≤8/重名/撞端口/挂载冲突);
    挂载列表给 raw 条目(canonical_* 在其中兜全量校验与排序)。
    """
    fused = fuse_mounts(spec, volumes, where, SIDECAR_ROLE)
    secctx = spec["security_context"]
    probe = spec["readiness_probe"]
    resources = spec["resources"]
    return {
        "name": spec["name"],
        "image": spec["image"],
        "port": (spec["ports"][0]["container_port"]
                 if spec["ports"] else None),
        "env": spec["env"],
        "env_from": spec["env_from"],
        "image_pull_policy": spec["image_pull_policy"],
        "cpu_request": resources["cpu_request"],
        "memory_request": resources["memory_request"],
        "cpu_limit": resources["cpu_limit"],
        "memory_limit": resources["memory_limit"],
        "privileged": secctx["privileged"],
        "capabilities_add": secctx["capabilities_add"],
        "capabilities_drop": secctx["capabilities_drop"],
        "seccomp_unconfined": secctx["seccomp_unconfined"],
        "apparmor_unconfined": secctx["apparmor_unconfined"],
        "run_as_user": secctx["run_as_user"],
        "run_as_group": secctx["run_as_group"],
        "host_path_mounts": fused["host_path_mounts"],
        "configmap_mounts": fused["configmap_mounts"],
        "pvc_mounts": fused["pvc_mounts"],
        "readiness_probe_type": probe["probe_type"],
        "readiness_path": probe["path"],
        "readiness_initial_delay": probe["initial_delay"],
        "readiness_period": probe["period"],
        "readiness_timeout_seconds": probe["timeout"],
    }


# -------------------------------------------------------------- volumes 列存取

def volumes_to_column(volumes: dict[str, dict[str, Any]]) -> Optional[list[dict]]:
    """内部卷映射 → 模板行 volumes JSON 列(按名排序,形态确定)。None 语义 = 无卷。"""
    if not volumes:
        return None
    return [volumes[name] for name in sorted(volumes)]


def volumes_from_column(value: Any) -> dict[str, dict[str, Any]]:
    """模板行 volumes JSON 列 → 内部卷映射(坏值防御:非列表/坏项跳过告警)。"""
    if not isinstance(value, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in value:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) \
                and isinstance(entry.get("kind"), str):
            out[entry["name"]] = entry
    return out


# -------------------------------------------------------------- DB 行转换

_CONTAINER_SECTION_COLUMNS = (
    "ports", "env", "env_from", "resources", "volume_mounts",
    "security_context", "readiness_probe",
)


def container_row_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """内部规范形 → DB 行列 dict(jiuwenclaw_id/时间戳由调用方补)。"""
    row = {
        "container_id": spec["container_id"],
        "name": spec["name"],
        "image": spec["image"],
        "image_pull_policy": spec["image_pull_policy"],
    }
    for column in _CONTAINER_SECTION_COLUMNS:
        row[column] = spec.get(column)
    return row


def container_spec_from_row(row: Any) -> Optional[dict[str, Any]]:
    """DB 行 → 内部规范形(行 None → None;段落列坏值防御回默认形态)。

    产物形态与 parse_container_spec 一致(同一直径),供水合投影复用。
    """
    if row is None:
        return None
    ports = getattr(row, "ports", None)
    if not isinstance(ports, list):
        ports = None
    else:
        ports = [
            {"name": p.get("name"), "container_port": p.get("container_port")}
            for p in ports if isinstance(p, dict)] or None
    env = getattr(row, "env", None)
    if not isinstance(env, dict):
        env = {}
    env_from = getattr(row, "env_from", None)
    if not isinstance(env_from, list):
        env_from = None
    resources = getattr(row, "resources", None)
    if not isinstance(resources, dict):
        resources = {}
    resources = {key: resources.get(key) for key in (
        "cpu_request", "memory_request", "cpu_limit", "memory_limit")}
    volume_mounts = getattr(row, "volume_mounts", None)
    if not isinstance(volume_mounts, list):
        volume_mounts = []
    secctx = getattr(row, "security_context", None)
    if not isinstance(secctx, dict):
        secctx = {}
    secctx = {key: secctx.get(key, default) for key, default in (
        ("run_as_user", None), ("run_as_group", None), ("privileged", False),
        ("capabilities_add", []), ("capabilities_drop", []),
        ("seccomp_unconfined", False), ("apparmor_unconfined", False))}
    probe = getattr(row, "readiness_probe", None)
    if not isinstance(probe, dict):
        probe = {}
    probe = {key: probe.get(key, default) for key, default in (
        ("probe_type", None), ("path", "/health"), ("initial_delay", 5),
        ("period", 10), ("timeout", 3))}
    return {
        "container_id": getattr(row, "container_id", None),
        "name": getattr(row, "name", None) or "",
        "image": getattr(row, "image", None) or "",
        "image_pull_policy": getattr(row, "image_pull_policy", None)
                             or "IfNotPresent",
        "ports": ports,
        "env": env,
        "env_from": env_from,
        "resources": resources,
        "volume_mounts": volume_mounts,
        "security_context": secctx,
        "readiness_probe": probe,
    }
