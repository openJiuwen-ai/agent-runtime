# coding: utf-8
"""container_spec 纯函数层测试:wire 解析矩阵/卷 join/投影/指纹承重断言。

承重红线:主容器投影缺省 == Template 默认(不漂指纹);sidecar 投影 ==
sidecars.py 既有规范形输出(逐字节);fused 挂载 == mounts.py 规范形。
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.errors import InvalidParams
from agent_runtime.session_manager.container_spec import (
    MAIN_ROLE,
    SIDECAR_ROLE,
    canonical_volumes,
    container_row_from_spec,
    container_spec_from_row,
    fuse_mounts,
    main_template_kwargs,
    parse_container_spec,
    sidecar_wire_input,
)
from agent_runtime.session_manager.models import Template
from agent_runtime.sidecars import validate_sidecars

# K8s wire 全量主容器样例(与 wire 契约文档同形态)
MAIN_FULL = {
    "container_id": "c-agent-main-1",
    "name": "agent",
    "image": "agentserver:2.1",
    "imagePullPolicy": "IfNotPresent",
    "ports": [{"name": "sse", "containerPort": 8086},
              {"name": "http", "containerPort": 9000}],
    "env": [{"name": "AGENT_HTTP_PORT", "value": "8086"}],
    "envFrom": [{"prefix": "DB_", "secretRef": {"name": "agent-secret"}},
                {"configMapRef": {"name": "agent-cm", "optional": True}}],
    "resources": {"requests": {"cpu": "500m", "memory": "1Gi"},
                  "limits": {"cpu": "2", "memory": "4Gi"}},
    "volumeMounts": [{"name": "data", "mountPath": "/var/lib/agent"},
                     {"name": "nfs", "mountPath": "/mnt/nfs"}],
    "securityContext": {"runAsUser": 1000, "runAsGroup": 1000},
    "readinessProbe": {"httpGet": {"path": "/api/v1/health", "port": 8086},
                       "initialDelaySeconds": 6, "periodSeconds": 7},
}

MAIN_VOLUMES = {
    "nfs": {"name": "nfs", "nfs": {"server": "10.0.0.1", "path": "/export"}},
    "data": {"name": "data",
             "persistentVolumeClaim": {"claimName": "agent-data"}},
}

# 与 MAIN_FULL 同值的 legacy 内联 sidecar(24 键形态,对照基准)
LEGACY_BOX = {
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "port": 8321,
    "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321"},
    "cpu_request": "100m",
    "memory_limit": "1Gi",
    "privileged": True,
    "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
    "seccomp_unconfined": True,
    "apparmor_unconfined": True,
    "host_path_mounts": [
        {"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"}],
    "readiness_probe_type": "tcp",
    "readiness_initial_delay": 10,
    "readiness_period": 5,
}

K8S_BOX = {
    "container_id": "c-box-1",
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "ports": [{"containerPort": 8321}],
    "env": [{"name": "JIUWENBOX_LISTEN", "value": "tcp://0.0.0.0:8321"}],
    "resources": {"requests": {"cpu": "100m"}, "limits": {"memory": "1Gi"}},
    "volumeMounts": [{"name": "cgroup", "mountPath": "/sys/fs/cgroup"}],
    "securityContext": {
        "privileged": True,
        "capabilities": {"add": ["SYS_ADMIN", "NET_ADMIN"]},
        "seccompProfile": {"type": "Unconfined"},
        "appArmorProfile": {"type": "Unconfined"},
    },
    "readinessProbe": {"tcpSocket": {"port": 8321},
                       "initialDelaySeconds": 10, "periodSeconds": 5},
}

BOX_VOLUMES = {
    "cgroup": {"name": "cgroup",
               "hostPath": {"path": "/sys/fs/cgroup"}},
}


def _validate_sidecars(items, **kw):
    kw.setdefault("container_name", "agent")
    kw.setdefault("sse_port", 8086)
    kw.setdefault("container_port", 8086)
    return validate_sidecars(items, **kw)


# -------------------------------------------------------------- 主容器投影(承重)

def test_main_container_all_fields_roundtrip():
    spec = parse_container_spec(MAIN_FULL, "containers[0]", role=MAIN_ROLE)
    volumes = canonical_volumes(list(MAIN_VOLUMES.values()), "volumes")
    kwargs = main_template_kwargs(spec, volumes, "containers[0]")
    assert kwargs == {
        "container_name": "agent",
        "agent_image": "agentserver:2.1",
        "image_pull_policy": "IfNotPresent",
        "sse_port": 8086,
        "container_port": 9000,
        "agent_env": {"AGENT_HTTP_PORT": "8086"},
        "agent_env_from": [
            {"prefix": "DB_",
             "secret_ref": {"name": "agent-secret", "optional": False}},
            {"prefix": None,
             "config_map_ref": {"name": "agent-cm", "optional": True}}],
        "agent_cpu_request": "500m",
        "agent_memory_request": "1Gi",
        "agent_cpu_limit": "2",
        "agent_memory_limit": "4Gi",
        "run_as_user": 1000,
        "run_as_group": 1000,
        "health_path": "/api/v1/health",
        "readiness_initial_delay": 6,
        "readiness_period": 7,
        "agent_host_path_mounts": None,
        "agent_configmap_mounts": None,
        "agent_pvc_mounts": [
            {"claim_name": "agent-data", "mount_path": "/var/lib/agent",
             "read_only": False}],
        "nfs_server": "10.0.0.1",
        "nfs_path": "/export",
        "nfs_mount_path": "/mnt/nfs",
    }


def test_main_container_defaults_match_template_defaults():
    """缺省落定与 Template 默认逐项相等 → 同值必同 deploy_ver(指纹承重)。"""
    spec = parse_container_spec(
        {"container_id": "c", "image": "img:1"}, "c", role=MAIN_ROLE)
    kwargs = main_template_kwargs(spec, {}, "c")
    defaults = Template(template_id="t")
    for key, value in kwargs.items():
        if key == "agent_image":
            continue  # 新契约必填(Template 缺省 "" 不适用)
        assert value == getattr(defaults, key), key
    assert (Template(template_id="t", **kwargs).deploy_ver()
            == Template(template_id="t", agent_image="img:1").deploy_ver())


def test_main_http_port_defaults_to_sse():
    spec = parse_container_spec(
        {"container_id": "c", "image": "i:1",
         "ports": [{"name": "sse", "containerPort": 8086}]},
        "c", role=MAIN_ROLE)
    assert main_template_kwargs(spec, {}, "c")["container_port"] == 8086
    # sse 端口本身缺省 8080
    spec2 = parse_container_spec({"container_id": "c", "image": "i:1"},
                                 "c", role=MAIN_ROLE)
    kwargs2 = main_template_kwargs(spec2, {}, "c")
    assert kwargs2["sse_port"] == 8080 and kwargs2["container_port"] == 8080


# -------------------------------------------------------------- sidecar 投影(承重)

def test_sidecar_projection_byte_identical_to_canonical():
    """K8s wire sidecar → 投影 == legacy 24 键输入的规范形,逐字节相等。"""
    expected = _validate_sidecars([LEGACY_BOX])[0]
    spec = parse_container_spec(K8S_BOX, "containers[1]", role=SIDECAR_ROLE)
    volumes = canonical_volumes(list(BOX_VOLUMES.values()), "volumes")
    projected = _validate_sidecars(
        [sidecar_wire_input(spec, volumes, "containers[1]")])[0]
    assert projected == expected
    assert (json.dumps(projected, sort_keys=True, ensure_ascii=False)
            == json.dumps(expected, sort_keys=True, ensure_ascii=False))


def test_sidecar_empty_collections_survive_projection():
    """空集合语义:env 恒 dict、mounts/caps 恒 list(空也进指纹)。"""
    spec = parse_container_spec(
        {"container_id": "c", "name": "box", "image": "x:1"},
        "c", role=SIDECAR_ROLE)
    wire_input = sidecar_wire_input(spec, {}, "c")
    assert wire_input["env"] == {}
    assert wire_input["host_path_mounts"] == []
    assert wire_input["capabilities_add"] == []
    canonical = _validate_sidecars([wire_input])[0]
    assert canonical["env"] == {}
    assert canonical["host_path_mounts"] == []
    assert canonical["capabilities_add"] == []
    assert "env_from" not in canonical


def test_sidecar_env_from_projected():
    spec = parse_container_spec(
        {"container_id": "c", "name": "box", "image": "x:1",
         "envFrom": [{"secretRef": {"name": "s"}}]},
        "c", role=SIDECAR_ROLE)
    canonical = _validate_sidecars([sidecar_wire_input(spec, {}, "c")])[0]
    assert canonical["env_from"] == [
        {"prefix": None, "secret_ref": {"name": "s", "optional": False}}]


# -------------------------------------------------------------- wire 拒绝矩阵

def test_unknown_container_keys_rejected():
    with pytest.raises(InvalidParams, match=r"unknown keys.*command"):
        parse_container_spec(
            {"container_id": "c", "image": "i:1", "command": ["/bin/sh"]},
            "containers[0]", role=MAIN_ROLE)


@pytest.mark.parametrize("item,match", [
    ({"image": "i:1"}, r"container_id"),                                  # 缺 id
    ({"container_id": "c" * 101, "image": "i:1"}, r"container_id"),       # 超长
    ({"container_id": "c", "image": ""}, r"image"),                       # 空镜像
    ({"container_id": "c", "image": "i:1", "name": "Bad_Name"}, r"DNS-1123"),
])
def test_main_container_basics_rejected(item, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(item, "containers[0]", role=MAIN_ROLE)


@pytest.mark.parametrize("ports,match", [
    ([{"containerPort": 8086}], r"only names 'sse' and 'http'"),  # 主容器无名端口
    ([{"name": "http", "containerPort": 8086}], r"exactly one port named 'sse'"),
    ([{"name": "sse", "containerPort": 8086},
      {"name": "sse", "containerPort": 8087}], r"exactly one port named 'sse'"),
    ([{"name": "debug", "containerPort": 8087}], r"only names 'sse' and 'http'"),
    ([{"name": "sse", "containerPort": 70000}], r"integer in \(1, 65535\]"),
    ([{"name": "sse", "containerPort": 8086, "protocol": "TCP"}], r"unknown keys"),
])
def test_main_port_rules_rejected(ports, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "image": "i:1", "ports": ports},
            "c", role=MAIN_ROLE)


@pytest.mark.parametrize("ports,match", [
    ([{"name": "sse", "containerPort": 8321}], r"must be null for a sidecar"),
    ([{"containerPort": 1}, {"containerPort": 2}], r"at most one port"),
])
def test_sidecar_port_rules_rejected(ports, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "name": "box", "image": "i:1",
             "ports": ports},
            "c", role=SIDECAR_ROLE)


@pytest.mark.parametrize("env,match", [
    ([{"name": "A", "value": "1"}, {"name": "A", "value": "2"}], r"duplicates"),
    ([{"name": "A", "value": 3}], r"value must be a string"),
    ([{"name": "", "value": "1"}], r"non-empty string"),
    ([{"name": "A"}], r"value must be a string"),
    ([{"name": "A", "value": "1", "valueFrom": {}}], r"unknown keys"),
])
def test_env_rules_rejected(env, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "image": "i:1", "env": env},
            "c", role=MAIN_ROLE)


@pytest.mark.parametrize("resources,match", [
    ({"request": {"cpu": "1"}}, r"unknown keys"),
    ({"requests": {"gpu": "1"}}, r"unknown keys"),
    ({"requests": {"cpu": ""}}, r"non-empty string"),
    ("big", r"must be an object"),
])
def test_resource_rules_rejected(resources, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "image": "i:1", "resources": resources},
            "c", role=MAIN_ROLE)


def test_main_security_context_role_restriction():
    """主容器 securityContext 只许 runAs 两键(越角色 400,防静默丢特权)。"""
    with pytest.raises(InvalidParams, match=r"unknown keys.*privileged"):
        parse_container_spec(
            {"container_id": "c", "image": "i:1",
             "securityContext": {"privileged": True}},
            "c", role=MAIN_ROLE)
    with pytest.raises(InvalidParams, match=r"seccompProfile"):
        parse_container_spec(
            {"container_id": "c", "image": "i:1",
             "securityContext": {"seccompProfile": {"type": "Unconfined"}}},
            "c", role=MAIN_ROLE)


@pytest.mark.parametrize("profile,match", [
    ({"seccompProfile": {"type": "Localhost"}}, r"must be one of"),
    ({"seccompProfile": {"type": "Unconfined", "extra": 1}}, r"exactly key"),
    ({"capabilities": {"add": [""]}}, r"non-empty strings"),
    ({"runAsUser": -1}, r"integer in"),
    ({"runAsUser": True}, r"must be an integer"),
])
def test_sidecar_security_context_rules_rejected(profile, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "name": "box", "image": "i:1",
             "securityContext": profile},
            "c", role=SIDECAR_ROLE)


def test_seccomp_apparmor_type_mapping():
    spec = parse_container_spec(
        {"container_id": "c", "name": "box", "image": "i:1",
         "securityContext": {
             "seccompProfile": {"type": "Unconfined"},
             "appArmorProfile": {"type": "RuntimeDefault"}}},
        "c", role=SIDECAR_ROLE)
    sec = spec["security_context"]
    assert sec["seccomp_unconfined"] is True
    assert sec["apparmor_unconfined"] is False


@pytest.mark.parametrize("probe,match", [
    ({"tcpSocket": {"port": 8086}}, r"always httpGet"),
    ({"httpGet": {"path": "/h"}, "timeoutSeconds": 3},
     r"not supported on the main container"),
    ({"httpGet": {"path": "/h", "port": 9000}}, r"must equal the container port"),
    ({"httpGet": {"path": "/h"}, "tcpSocket": {"port": 8086}},
     r"mutually exclusive"),
    ({"initialDelaySeconds": -1}, r"integer in"),
    ({"periodSeconds": 0}, r"integer in"),
])
def test_main_probe_rules_rejected(probe, match):
    with pytest.raises(InvalidParams, match=match):
        parse_container_spec(
            {"container_id": "c", "image": "i:1",
             "ports": [{"name": "sse", "containerPort": 8086}],
             "readinessProbe": probe},
            "c", role=MAIN_ROLE)


def test_sidecar_probe_defaults_match_sidecar_canonical():
    """sidecar 探针缺省(period=10/timeout=3)与 _canonical_sidecar 默认逐项相等。"""
    spec = parse_container_spec(
        {"container_id": "c", "name": "box", "image": "i:1",
         "ports": [{"containerPort": 8321}]},
        "c", role=SIDECAR_ROLE)
    canonical = _validate_sidecars([sidecar_wire_input(spec, {}, "c")])[0]
    assert canonical["readiness_probe_type"] is None
    assert canonical["readiness_initial_delay"] == 5
    assert canonical["readiness_period"] == 10
    assert canonical["readiness_timeout_seconds"] == 3


# -------------------------------------------------------------- 卷 join

def test_volume_join_fused_mounts_canonical():
    """volumes×volumeMounts join → fused 形态 == mounts.py 规范形(排序/默认)。"""
    volumes = canonical_volumes([
        {"name": "nfs", "nfs": {"server": "10.0.0.1", "path": "/export"}},
        {"name": "cfg", "configMap": {"name": "agent-cm",
                                       "items": [{"key": "b", "path": "b.yaml"},
                                                 {"key": "a", "path": "a.yaml"}]}},
        {"name": "hp", "hostPath": {"path": "/mnt/host",
                                     "type": "DirectoryOrCreate"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": "agent-data"}},
    ], "volumes")
    spec = parse_container_spec(
        {"container_id": "c", "image": "i:1",
         "ports": [{"name": "sse", "containerPort": 8086}],
         "volumeMounts": [
             # 刻意乱序:规范形必须按 mount_path 排序
             {"name": "hp", "mountPath": "/zz"},
             {"name": "cfg", "mountPath": "/etc/agent"},
             {"name": "nfs", "mountPath": "/mnt/nfs"},
             {"name": "data", "mountPath": "/var/lib/agent"},
         ]}, "c", role=MAIN_ROLE)
    kwargs = main_template_kwargs(spec, volumes, "c")
    assert kwargs["agent_host_path_mounts"] == [
        {"host_path": "/mnt/host", "mount_path": "/zz", "read_only": False,
         "host_path_type": "DirectoryOrCreate"}]
    # configMap:read_only 缺省 true、items 按 key 排序
    assert kwargs["agent_configmap_mounts"] == [
        {"config_map_name": "agent-cm", "mount_path": "/etc/agent",
         "sub_path": None,
         "items": [{"key": "a", "path": "a.yaml"}, {"key": "b", "path": "b.yaml"}],
         "read_only": True}]
    assert kwargs["agent_pvc_mounts"] == [
        {"claim_name": "agent-data", "mount_path": "/var/lib/agent",
         "read_only": False}]
    assert kwargs["nfs_server"] == "10.0.0.1"
    assert kwargs["nfs_path"] == "/export"
    assert kwargs["nfs_mount_path"] == "/mnt/nfs"


def test_volume_join_read_only_overrides():
    volumes = canonical_volumes(
        [{"name": "cfg", "configMap": {"name": "cm"}},
          {"name": "hp", "hostPath": {"path": "/h"}}], "v")
    spec = parse_container_spec(
        {"container_id": "c", "image": "i:1",
         "volumeMounts": [
             {"name": "cfg", "mountPath": "/c", "readOnly": False},
             {"name": "hp", "mountPath": "/h2", "readOnly": True}]},
        "c", role=MAIN_ROLE)
    kwargs = main_template_kwargs(spec, volumes, "c")
    assert kwargs["agent_configmap_mounts"][0]["read_only"] is False
    assert kwargs["agent_host_path_mounts"][0]["read_only"] is True


@pytest.mark.parametrize("volumes,mounts,where_role,match", [
    # 悬挂引用
    ([], [{"name": "ghost", "mountPath": "/g"}], MAIN_ROLE, r"not defined"),
    # subPath 只许 configMap
    ([{"name": "hp", "hostPath": {"path": "/h"}}],
     [{"name": "hp", "mountPath": "/h", "subPath": "s"}], MAIN_ROLE,
     r"only supported on configMap"),
    # NFS readOnly 不支持
    ([{"name": "n", "nfs": {"server": "s"}}],
     [{"name": "n", "mountPath": "/n", "readOnly": True}], MAIN_ROLE,
     r"readOnly=true"),
    # NFS 不许 sidecar
    ([{"name": "n", "nfs": {"server": "s"}}],
     [{"name": "n", "mountPath": "/n"}], SIDECAR_ROLE, r"sidecar container"),
    # NFS 至多一个
    ([{"name": "n1", "nfs": {"server": "s"}},
      {"name": "n2", "nfs": {"server": "s"}}],
     [{"name": "n1", "mountPath": "/n1"}, {"name": "n2", "mountPath": "/n2"}],
     MAIN_ROLE, r"at most one nfs"),
])
def test_volume_join_rules_rejected(volumes, mounts, where_role, match):
    spec = parse_container_spec(
        {"container_id": "c", "name": "agent", "image": "i:1",
         "ports": ([{"name": "sse", "containerPort": 8086}]
                   if where_role == MAIN_ROLE else None),
         "volumeMounts": mounts},
        "c", role=where_role)
    with pytest.raises(InvalidParams, match=match):
        fuse_mounts(spec, canonical_volumes(volumes, "v"), "c", where_role)


@pytest.mark.parametrize("volumes,match", [
    ([{"hostPath": {"path": "/h"}}], r"DNS-1123"),   # 缺 name
    ([{"name": "hp", "hostPath": {"path": "/h"}, "nfs": {"server": "s"}}],
     r"exactly one volume source"),
    ([{"name": "Bad!", "hostPath": {"path": "/h"}}], r"DNS-1123"),
    ([{"name": "hp", "hostPath": {"path": "/h"}},
      {"name": "hp", "hostPath": {"path": "/h2"}}], r"duplicates"),
    ([{"name": "hp", "hostPath": {}}], r"non-empty string"),
    ([{"name": "n", "nfs": {}}], r"non-empty string"),
])
def test_canonical_volumes_rejected(volumes, match):
    with pytest.raises(InvalidParams, match=match):
        canonical_volumes(volumes, "volumes")


def test_canonical_volumes_none_is_empty():
    assert canonical_volumes(None, "v") == {}


# -------------------------------------------------------------- DB 行往返

def test_container_row_roundtrip():
    spec = parse_container_spec(MAIN_FULL, "c", role=MAIN_ROLE)
    row = container_row_from_spec(spec)
    assert set(row) == {
        "container_id", "name", "image", "image_pull_policy", "ports", "env",
        "env_from", "resources", "volume_mounts", "security_context",
        "readiness_probe"}
    from types import SimpleNamespace
    restored = container_spec_from_row(SimpleNamespace(**row))
    assert restored == spec


def test_container_spec_from_row_none_and_corrupt():
    from types import SimpleNamespace
    assert container_spec_from_row(None) is None
    # 坏段落防御:不抛,回默认形态
    bad = SimpleNamespace(container_id="c", name=None, image=None,
                          image_pull_policy=None, ports="x", env="x",
                          env_from="x", resources="x", volume_mounts="x",
                          security_context="x", readiness_probe="x")
    spec = container_spec_from_row(bad)
    assert spec["name"] == "" and spec["image"] == ""
    assert spec["ports"] is None and spec["env"] == {}
    assert spec["volume_mounts"] == []
    assert spec["readiness_probe"]["period"] == 10   # sidecar 缺省口径
