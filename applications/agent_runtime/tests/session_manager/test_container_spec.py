# coding: utf-8
"""container_spec 纯函数层测试:wire 解析矩阵/卷 join/投影/指纹承重断言。

承重红线:主容器投影缺省 == Template 默认(不漂指纹);sidecar 投影 ==
sidecars.py 既有规范形输出(逐字节);fused 挂载 == mounts.py 规范形。
"""

from __future__ import annotations


import pytest

from agent_runtime.containers import validate_pod_containers
from agent_runtime.errors import InvalidParams
from agent_runtime.session_manager.container_spec import (
    MAIN_ROLE,
    SIDECAR_ROLE,
    build_canonical,
    canonical_volumes,
    container_row_from_spec,
    container_spec_from_row,
    fuse_mounts,
    parse_container_spec,
)
from agent_runtime.session_manager.models import Template

# K8s wire 全量主容器样例(与 wire 契约文档同形态)
MAIN_FULL = {
    "container_id": "c-agent-main-1",
    "name": "agent",
    "image": "agentserver:2.1",
    "imagePullPolicy": "IfNotPresent",
    "command": ["/bin/agent", "--foreground"],
    "args": ["--port", "8086"],
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


# -------------------------------------------------------------- 主容器投影(承重)

def test_main_container_defaults_equal_omitted():
    """「显式给默认值」与「省略键」→ 同 canonical → 同 deploy_ver(指纹承重)。"""
    minimal = {"name": "agent", "image": "img:1"}
    explicit = build_canonical(parse_container_spec(
        {"container_id": "c", "image": "img:1"}, "c", role=MAIN_ROLE),
        {}, "c", role=MAIN_ROLE)
    omitted = build_canonical(parse_container_spec(
        {"container_id": "c", "image": "img:1", "name": "agent",
         "imagePullPolicy": "IfNotPresent"}, "c", role=MAIN_ROLE),
        {}, "c", role=MAIN_ROLE)
    assert explicit == omitted
    assert (Template(template_id="t", main_container=explicit).deploy_ver()
            == Template(template_id="t",
                        main_container=dict(minimal)).deploy_ver())


def test_main_ports_and_http_defaults():
    spec = parse_container_spec(
        {"container_id": "c", "image": "i:1",
         "ports": [{"name": "sse", "containerPort": 8086}]},
        "c", role=MAIN_ROLE)
    cont = build_canonical(spec, {}, "c", role=MAIN_ROLE)
    assert [p["name"] for p in cont["ports"]] == ["sse"]
    assert cont["ports"][0]["container_port"] == 8086
    # sse 端口本身缺省 8080
    cont2 = build_canonical(parse_container_spec(
        {"container_id": "c", "image": "i:1"}, "c", role=MAIN_ROLE),
        {}, "c", role=MAIN_ROLE)
    assert cont2["ports"] == [{"name": "sse", "container_port": 8080}]
    # http 端口号 == sse → canonical 丢弃(RM 渲染同名端口去重的约定)
    cont3 = build_canonical(parse_container_spec(
        {"container_id": "c", "image": "i:1",
         "ports": [{"name": "sse", "containerPort": 8086},
                   {"name": "http", "containerPort": 8086}]},
        "c", role=MAIN_ROLE), {}, "c", role=MAIN_ROLE)
    assert cont3["ports"] == [{"name": "sse", "container_port": 8086}]
    assert cont3 == cont


# -------------------------------------------------------------- sidecar 投影(承重)

def test_sidecar_empty_collections_survive_projection():
    """空集合语义:env 恒 dict、mounts/caps 恒 list(空也进指纹,恒为键)。"""
    cont = build_canonical(parse_container_spec(
        {"container_id": "c", "name": "box", "image": "x:1"},
        "c", role=SIDECAR_ROLE), {}, "c", role=SIDECAR_ROLE)
    assert cont["env"] == {}
    assert cont["host_path_mounts"] == []
    assert cont["security_context"]["capabilities_add"] == []
    assert cont["env_from"] is None  # 全键携带(条件键已废除)


def test_sidecar_env_from_projected():
    spec = parse_container_spec(
        {"container_id": "c", "name": "box", "image": "x:1",
         "envFrom": [{"secretRef": {"name": "s"}}]},
        "c", role=SIDECAR_ROLE)
    cont = build_canonical(spec, {}, "c", role=SIDECAR_ROLE)
    assert cont["env_from"] == [
        {"prefix": None, "secret_ref": {"name": "s", "optional": False}}]


def test_validate_pod_containers_accepts_built_canonical():
    """build_canonical 产物可直接过 validate_pod_containers(同直径收敛)。"""
    main = build_canonical(parse_container_spec(
        MAIN_FULL, "containers[0]", role=MAIN_ROLE),
        canonical_volumes(list(MAIN_VOLUMES.values()), "volumes"),
        "containers[0]", role=MAIN_ROLE)
    box = build_canonical(parse_container_spec(
        K8S_BOX, "containers[1]", role=SIDECAR_ROLE),
        canonical_volumes(list(BOX_VOLUMES.values()), "volumes"),
        "containers[1]", role=SIDECAR_ROLE)
    out_main, out_sidecars = validate_pod_containers(main, [box], "t")
    assert out_main == main and out_sidecars == [box]


# -------------------------------------------------------------- wire 拒绝矩阵

def test_unknown_container_keys_rejected():
    with pytest.raises(InvalidParams, match=r"unknown keys.*workingDir"):
        parse_container_spec(
            {"container_id": "c", "image": "i:1", "workingDir": "/w"},
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


def test_sidecar_probe_defaults_match_canonical():
    """sidecar 探针缺省(probe_type None/period=10/timeout=3)。"""
    cont = build_canonical(parse_container_spec(
        {"container_id": "c", "name": "box", "image": "i:1",
         "ports": [{"containerPort": 8321}]},
        "c", role=SIDECAR_ROLE), {}, "c", role=SIDECAR_ROLE)
    assert cont["readiness_probe"] == {"probe_type": None, "path": "/health",
                                       "initial_delay": 5, "period": 10,
                                       "timeout": 3}


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
    cont = build_canonical(spec, volumes, "c", role=MAIN_ROLE)
    assert cont["host_path_mounts"] == [
        {"host_path": "/mnt/host", "mount_path": "/zz", "read_only": False,
         "host_path_type": "DirectoryOrCreate"}]
    # configMap:read_only 缺省 true、items 按 key 排序
    assert cont["configmap_mounts"] == [
        {"config_map_name": "agent-cm", "mount_path": "/etc/agent",
         "sub_path": None,
         "items": [{"key": "a", "path": "a.yaml"}, {"key": "b", "path": "b.yaml"}],
         "read_only": True}]
    assert cont["pvc_mounts"] == [
        {"claim_name": "agent-data", "mount_path": "/var/lib/agent",
         "read_only": False}]
    assert cont["nfs_mounts"] == [{"server": "10.0.0.1", "path": "/export",
                                   "mount_path": "/mnt/nfs",
                                   "read_only": False}]


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
    cont = build_canonical(spec, volumes, "c", role=MAIN_ROLE)
    assert cont["configmap_mounts"][0]["read_only"] is False
    assert cont["host_path_mounts"][0]["read_only"] is True


@pytest.mark.parametrize("volumes,mounts,where_role,match", [
    # 悬挂引用
    ([], [{"name": "ghost", "mountPath": "/g"}], MAIN_ROLE, r"not defined"),
    # subPath 只许 configMap
    ([{"name": "hp", "hostPath": {"path": "/h"}}],
     [{"name": "hp", "mountPath": "/h", "subPath": "s"}], MAIN_ROLE,
     r"only supported on configMap"),
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



def test_volume_join_nfs_same_as_pvc():
    """NFS 与 PVC 同构(上游 bef82fc4 放宽):主/sidecar 均可挂、条数不限、
    readOnly 透传——K8s 语义,不再自设窄约束。"""
    volumes = canonical_volumes([
        {"name": "n1", "nfs": {"server": "10.0.0.1", "path": "/export"}},
        {"name": "n2", "nfs": {"server": "10.0.0.2"}},
    ], "volumes")
    spec = parse_container_spec(
        {"container_id": "c", "name": "agent", "image": "i:1",
         "ports": [{"name": "sse", "containerPort": 8086}],
         "volumeMounts": [{"name": "n1", "mountPath": "/mnt/n1",
                           "readOnly": True},
                          {"name": "n2", "mountPath": "/mnt/n2"}]},
        "c", role=MAIN_ROLE)
    cont = build_canonical(spec, volumes, "c", role=MAIN_ROLE)
    assert cont["nfs_mounts"] == [   # 规范形按 mount_path 升序
        {"server": "10.0.0.1", "path": "/export", "mount_path": "/mnt/n1",
         "read_only": True},
        {"server": "10.0.0.2", "path": None, "mount_path": "/mnt/n2",
         "read_only": False}]
    # sidecar 挂 NFS 合法
    sc_spec = parse_container_spec(
        {"container_id": "c2", "name": "box", "image": "i:1",
         "volumeMounts": [{"name": "n1", "mountPath": "/box/n1"}]},
        "c2", role=SIDECAR_ROLE)
    sc = build_canonical(sc_spec, volumes, "c2", role=SIDECAR_ROLE)
    assert sc["nfs_mounts"] == [
        {"server": "10.0.0.1", "path": "/export", "mount_path": "/box/n1",
         "read_only": False}]

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
        "container_id", "name", "image", "image_pull_policy", "command",
        "args", "ports", "env", "env_from", "resources", "volume_mounts",
        "security_context", "readiness_probe"}
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


# -------------------------------------------------------------- build_canonical(C2 内核)

def test_build_canonical_main_golden():
    """wire 主容器 + volumes → canonical 黄金 dict(13 键,指纹/传输/渲染同形)。"""
    from agent_runtime.session_manager.container_spec import build_canonical
    spec = parse_container_spec(MAIN_FULL, "containers[0]", role=MAIN_ROLE)
    volumes = canonical_volumes(list(MAIN_VOLUMES.values()), "volumes")
    cont = build_canonical(spec, volumes, "containers[0]", role=MAIN_ROLE)
    assert cont == {
        "name": "agent",
        "image": "agentserver:2.1",
        "image_pull_policy": "IfNotPresent",
        "command": ["/bin/agent", "--foreground"],
        "args": ["--port", "8086"],
        "ports": [{"name": "sse", "container_port": 8086},
                  {"name": "http", "container_port": 9000}],
        "env": {"AGENT_HTTP_PORT": "8086"},
        "env_from": [
            {"prefix": "DB_",
             "secret_ref": {"name": "agent-secret", "optional": False}},
            {"prefix": None,
             "config_map_ref": {"name": "agent-cm", "optional": True}}],
        "resources": {"cpu_request": "500m", "memory_request": "1Gi",
                      "cpu_limit": "2", "memory_limit": "4Gi"},
        "host_path_mounts": [],
        "configmap_mounts": [],
        "pvc_mounts": [{"claim_name": "agent-data",
                        "mount_path": "/var/lib/agent", "read_only": False}],
        "nfs_mounts": [{"server": "10.0.0.1", "path": "/export",
                        "mount_path": "/mnt/nfs", "read_only": False}],
        "security_context": {"run_as_user": 1000, "run_as_group": 1000,
                             "privileged": False, "capabilities_add": [],
                             "capabilities_drop": [],
                             "seccomp_unconfined": False,
                             "apparmor_unconfined": False},
        "readiness_probe": {"probe_type": "http", "path": "/api/v1/health",
                            "initial_delay": 6, "period": 7, "timeout": None},
    }


def test_build_canonical_defaults_and_idempotence():
    from agent_runtime.containers import MAIN_PROBE_DEFAULT
    from agent_runtime.session_manager.container_spec import build_canonical
    spec = parse_container_spec({"container_id": "c", "image": "x:1"},
                                "containers[0]", role=MAIN_ROLE)
    cont = build_canonical(spec, {}, "containers[0]", role=MAIN_ROLE)
    assert cont["name"] == "agent"
    assert cont["ports"] == [{"name": "sse", "container_port": 8080}]
    assert cont["readiness_probe"] == dict(MAIN_PROBE_DEFAULT)
    assert cont["nfs_mounts"] == [] and cont["env_from"] is None
    assert cont["command"] is None and cont["args"] is None
    # 幂等:canonical 再过 build_canonical 的收口层不变
    from agent_runtime.containers import canonical_container
    assert canonical_container(cont, "w", role=MAIN_ROLE) == cont


def test_build_canonical_sidecar_matches_containers_module():
    """wire sidecar → canonical == containers.canonical_container 直构(同直径)。"""
    from agent_runtime.containers import canonical_container
    from agent_runtime.session_manager.container_spec import build_canonical
    spec = parse_container_spec(K8S_BOX, "containers[1]", role=SIDECAR_ROLE)
    volumes = canonical_volumes(list(BOX_VOLUMES.values()), "volumes")
    cont = build_canonical(spec, volumes, "containers[1]",
                           role=SIDECAR_ROLE)
    assert cont == canonical_container({
        "name": "jiuwenbox",
        "image": "jiuwenbox-amd64:0.0.1",
        "ports": [{"name": None, "container_port": 8321}],
        "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321"},
        "resources": {"cpu_request": "100m", "memory_request": None,
                      "cpu_limit": None, "memory_limit": "1Gi"},
        "host_path_mounts": [{"host_path": "/sys/fs/cgroup",
                              "mount_path": "/sys/fs/cgroup"}],
        "security_context": {"run_as_user": None, "run_as_group": None,
                             "privileged": True,
                             "capabilities_add": ["NET_ADMIN", "SYS_ADMIN"],
                             "capabilities_drop": [],
                             "seccomp_unconfined": True,
                             "apparmor_unconfined": True},
        "readiness_probe": {"probe_type": "tcp", "path": "/health",
                            "initial_delay": 10, "period": 5, "timeout": 3},
    }, "w", role=SIDECAR_ROLE)
