# coding: utf-8
"""_build_pod_body 多容器渲染测试(单容器黄金断言 + sidecar 全量渲染)。

mock 手法:_V1 记录型替身注入 client._client——_build_pod_body 只做 kwargs
透传,断言直接读 .kwargs 链,零环境依赖(不依赖 kubernetes_asyncio 安装)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.errors import DeployFailed
from agent_runtime.resource_manager.k8s import (
    RealK8sPodClient,
    _host_path_volume_name,
)
from agent_runtime.sidecars import validate_sidecars

JIUWENBOX = {
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "port": 8321,
    "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321",
            "JIUWENBOX_POLICY_PATH": "/app/configs/enterprise-policy.yaml"},
    "cpu_request": "100m",
    "memory_limit": "1Gi",
    "privileged": True,
    "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
    "seccomp_unconfined": True,
    "apparmor_unconfined": True,
    "host_path_mounts": [
        {"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"},
    ],
    "readiness_probe_type": "tcp",
    "readiness_initial_delay": 10,
    "readiness_period": 5,
}


class _V1:
    """记录型替身:构造参数全存 .kwargs 供断言。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _fake_client_module() -> SimpleNamespace:
    names = (
        "V1Container", "V1Pod", "V1ObjectMeta", "V1PodSpec", "V1Volume",
        "V1NFSVolumeSource", "V1VolumeMount", "V1ResourceRequirements",
        "V1ContainerPort", "V1Probe", "V1HTTPGetAction", "V1TCPSocketAction",
        "V1EnvVar", "V1SecurityContext", "V1Capabilities", "V1SeccompProfile",
        "V1HostPathVolumeSource", "V1ConfigMapVolumeSource", "V1KeyToPath",
        "V1PersistentVolumeClaimVolumeSource",
    )
    return SimpleNamespace(**{name: _V1 for name in names})


@pytest.fixture
def client() -> RealK8sPodClient:
    c = RealK8sPodClient()
    c._client = _fake_client_module()
    return c


def _base_spec(**overrides) -> dict:
    spec = {
        "agent_image": "agentserver:1.0",
        "namespace": "default",
        "sse_port": 8086,
        "container_port": 8086,
        "container_name": "agent",
        "nfs_server": "nfs.example",
        "nfs_path": "/export",
        "nfs_mount_path": "/data",
        "health_path": "/health",
        "agent_env": {"AGENT_HTTP_ENABLED": "true"},
    }
    spec.update(overrides)
    return spec


def _sidecars(validated: list[dict]) -> list[dict]:
    return validate_sidecars(validated, container_name="agent",
                             sse_port=8086, container_port=8086)


# -------------------------------------------------------------- 单容器黄金断言

def test_build_pod_body_without_sidecars_unchanged(client):
    """无 sidecars:恰 1 容器、annotations=None、volumes 只含 nfs——与历史一致。"""
    pod = client._build_pod_body("pod-1", _base_spec())
    meta, spec = pod.kwargs["metadata"].kwargs, pod.kwargs["spec"].kwargs
    assert meta["annotations"] is None
    containers = spec["containers"]
    assert len(containers) == 1
    assert containers[0].kwargs["name"] == "agent"
    volume_names = [v.kwargs["name"] for v in spec["volumes"]]
    assert volume_names == ["pod-1-nfs"]
    assert spec["restart_policy"] == "Always"
    # 主容器探针/端口/env 不受 sidecar 改动影响
    main = containers[0].kwargs
    assert main["readiness_probe"].kwargs["http_get"].kwargs == {
        "path": "/health", "port": 8086}
    assert [p.kwargs["container_port"] for p in main["ports"]] == [8086]


# -------------------------------------------------------------- sidecar 渲染

def test_build_pod_body_renders_full_sidecar(client):
    """jiuwenbox 全量:双容器 + 特权安全上下文 + hostPath 卷 + apparmor annotation。"""
    spec = _base_spec(sidecars=_sidecars([JIUWENBOX]))
    pod = client._build_pod_body("pod-1", spec)
    meta, pod_spec = pod.kwargs["metadata"].kwargs, pod.kwargs["spec"].kwargs

    containers = pod_spec["containers"]
    assert [c.kwargs["name"] for c in containers] == ["agent", "jiuwenbox"]
    box = containers[1].kwargs
    assert box["image"] == "jiuwenbox-amd64:0.0.1"
    assert box["image_pull_policy"] == "IfNotPresent"
    # 端口声明性无名
    assert [p.kwargs for p in box["ports"]] == [{"container_port": 8321}]
    # env 逐项
    assert {(e.kwargs["name"], e.kwargs["value"]) for e in box["env"]} == {
        ("JIUWENBOX_LISTEN", "tcp://0.0.0.0:8321"),
        ("JIUWENBOX_POLICY_PATH", "/app/configs/enterprise-policy.yaml"),
    }
    # 安全上下文:特权 + caps + seccomp unconfined
    sec = box["security_context"].kwargs
    assert sec["privileged"] is True
    assert sec["capabilities"].kwargs["add"] == ["SYS_ADMIN", "NET_ADMIN"]
    assert sec["seccomp_profile"].kwargs == {"type": "Unconfined"}
    # apparmor → Pod annotation(不是 security_context)
    assert meta["annotations"] == {
        "container.apparmor.security.beta.kubernetes.io/jiuwenbox": "unconfined"}
    # tcp readiness 探针参数
    probe = box["readiness_probe"].kwargs
    assert probe["tcp_socket"].kwargs == {"port": 8321}
    assert probe["initial_delay_seconds"] == 10
    assert probe["period_seconds"] == 5
    assert probe["timeout_seconds"] == 3
    # 独立资源配额
    assert box["resources"].kwargs == {
        "requests": {"cpu": "100m"}, "limits": {"memory": "1Gi"}}
    # hostPath 卷:Pod 级卷 + 容器挂载,卷名 hp- 前缀双索引
    vol_names = [v.kwargs["name"] for v in pod_spec["volumes"]]
    assert "hp-jiuwenbox-0-0" in vol_names and "pod-1-nfs" in vol_names
    hp = [v for v in pod_spec["volumes"]
          if v.kwargs["name"] == "hp-jiuwenbox-0-0"][0]
    assert hp.kwargs["host_path"].kwargs == {
        "path": "/sys/fs/cgroup", "type": None}
    assert box["volume_mounts"][0].kwargs == {
        "name": "hp-jiuwenbox-0-0", "mount_path": "/sys/fs/cgroup",
        "read_only": False}
    assert pod_spec["restart_policy"] == "Always"


def test_build_pod_body_sidecar_readiness_http(client):
    sc = dict(JIUWENBOX, readiness_probe_type="http", port=8321,
              readiness_path="/box/health")
    spec = _base_spec(sidecars=_sidecars([sc]))
    pod = client._build_pod_body("pod-1", spec)
    probe = pod.kwargs["spec"].kwargs["containers"][1].kwargs["readiness_probe"]
    assert probe.kwargs["http_get"].kwargs == {"path": "/box/health", "port": 8321}


def test_build_pod_body_sidecar_without_port(client):
    """无 port sidecar:ports=None、无探针(纯后台容器)。"""
    sc = {"name": "logtail", "image": "logtail:1"}
    spec = _base_spec(sidecars=_sidecars([sc]))
    pod = client._build_pod_body("pod-1", spec)
    box = pod.kwargs["spec"].kwargs["containers"][1].kwargs
    assert box["ports"] is None
    assert box["readiness_probe"] is None
    assert box["security_context"] is None  # 无任何安全字段 → 不生成


def test_build_pod_body_rejects_port_conflict(client):
    """脏缓存(绕过 SM 校验的 pod_spec):sidecar port 撞 sse_port → DeployFailed。"""
    spec = _base_spec(sidecars=[dict(JIUWENBOX, port=8086)])  # 原始 dict,未走校验
    with pytest.raises(DeployFailed, match="sidecars invalid"):
        client._build_pod_body("pod-1", spec)


def test_build_pod_body_skips_corrupt_cached_sidecars(client):
    """脏缓存坏项:normalize 兜底丢弃,只渲染合法项。"""
    spec = _base_spec(sidecars=[{"garbage": 1}, dict(JIUWENBOX)])
    pod = client._build_pod_body("pod-1", spec)
    names = [c.kwargs["name"] for c in pod.kwargs["spec"].kwargs["containers"]]
    assert names == ["agent", "jiuwenbox"]


# -------------------------------------------------------------- 卷名规则

def test_build_pod_body_renders_main_container_mounts(client):
    """主容器三种挂载:ConfigMap(sub_path+items)/hostPath/PVC 卷与挂载点。"""
    from agent_runtime.mounts import validate_agent_mounts

    hp, cm, pvc = validate_agent_mounts(
        [{"host_path": "/host/cfg", "mount_path": "/etc/host"}],
        [{"config_map_name": "agent-cm", "mount_path": "/etc/agent/config.yaml",
          "sub_path": "config.yaml",
          "items": [{"key": "k1", "path": "config.yaml"}]}],
        [{"claim_name": "agent-data", "mount_path": "/data"}],
        nfs_mount_path="/nfs",
    )
    spec = _base_spec(nfs_mount_path="/nfs",
                      agent_host_path_mounts=hp,
                      agent_configmap_mounts=cm,
                      agent_pvc_mounts=pvc)
    pod = client._build_pod_body("pod-1", spec)
    pod_spec = pod.kwargs["spec"].kwargs
    vols = {v.kwargs["name"]: v.kwargs for v in pod_spec["volumes"]}
    # NFS(既有)+ 三种新卷共存;主容器卷名 {hp,cm,pvc}-agent-0-{mount_idx}
    assert set(vols) == {"pod-1-nfs", "cm-agent-0-0", "hp-agent-0-0", "pvc-agent-0-0"}
    cm_vol = vols["cm-agent-0-0"]["config_map"].kwargs
    assert cm_vol["name"] == "agent-cm"
    assert [e.kwargs for e in cm_vol["items"]] == [{"key": "k1",
                                                    "path": "config.yaml"}]
    assert vols["hp-agent-0-0"]["host_path"].kwargs == {
        "path": "/host/cfg", "type": None}
    assert vols["pvc-agent-0-0"]["persistent_volume_claim"].kwargs == {
        "claim_name": "agent-data", "read_only": False}
    # 主容器 volumeMounts:NFS + cm(sub_path+只读默认 True)+ hp + pvc
    main = pod_spec["containers"][0].kwargs
    mounts = {m.kwargs["mount_path"]: m.kwargs for m in main["volume_mounts"]}
    assert set(mounts) == {"/nfs", "/etc/host", "/etc/agent/config.yaml", "/data"}
    assert mounts["/etc/agent/config.yaml"] == {
        "name": "cm-agent-0-0", "mount_path": "/etc/agent/config.yaml",
        "sub_path": "config.yaml", "read_only": True}
    assert mounts["/etc/host"]["read_only"] is False


def test_build_pod_body_renders_sidecar_configmap_and_pvc(client):
    from agent_runtime.sidecars import validate_sidecars

    sc = dict(JIUWENBOX, configmap_mounts=[
        {"config_map_name": "box-policy",
         "mount_path": "/etc/jiuwenbox/policy.yaml", "sub_path": "policy.yaml"}],
        pvc_mounts=[{"claim_name": "box-data", "mount_path": "/var/lib/box"}])
    sidecars = validate_sidecars([sc], container_name="agent",
                                 sse_port=8086, container_port=8086)
    spec = _base_spec(sidecars=sidecars)
    pod = client._build_pod_body("pod-1", spec)
    pod_spec = pod.kwargs["spec"].kwargs
    vols = {v.kwargs["name"]: v.kwargs for v in pod_spec["volumes"]}
    assert "cm-jiuwenbox-0-0" in vols and "hp-jiuwenbox-0-0" in vols
    assert vols["cm-jiuwenbox-0-0"]["config_map"].kwargs == {
        "name": "box-policy", "items": None}
    box = pod_spec["containers"][1].kwargs
    mounts = {m.kwargs["mount_path"]: m.kwargs for m in box["volume_mounts"]}
    assert mounts["/etc/jiuwenbox/policy.yaml"]["sub_path"] == "policy.yaml"
    assert mounts["/var/lib/box"]["name"] == "pvc-jiuwenbox-0-0"


@pytest.mark.parametrize("name,idx,mount_idx,expected", [
    ("jiuwenbox", 0, 0, "hp-jiuwenbox-0-0"),
    ("JiuwenBox", 1, 2, "hp-jiuwenbox-1-2"),      # 大写净化
    ("a" * 80, 0, 0, "hp-" + "a" * 56 + "-0-0"),  # 截断后整体 =63
    ("", 3, 0, "hp-c3-3-0"),                      # 空名回退 c{idx},仍带双索引
])
def test_host_path_volume_name_rules(name, idx, mount_idx, expected):
    out = _host_path_volume_name(name, idx, mount_idx)
    assert out == expected
    assert len(out) <= 63
    assert out == out.lower()


# ---------------------------------------------- 主容器 securityContext / node_name

def test_build_pod_body_main_security_context(client):
    """主容器 run_as_user/run_as_group → securityContext;未给则不设键(镜像 USER 生效)。"""
    spec = _base_spec(run_as_user=1000, run_as_group=1000)
    main = client._build_pod_body("pod-1", spec).kwargs["spec"].kwargs[
        "containers"][0].kwargs
    assert main["security_context"].kwargs == {
        "run_as_user": 1000, "run_as_group": 1000}
    # 只给 user 不给 group:半渲染
    main = client._build_pod_body(
        "pod-1", _base_spec(run_as_user=1000)).kwargs["spec"].kwargs[
        "containers"][0].kwargs
    assert main["security_context"].kwargs == {"run_as_user": 1000}
    # 默认:不设键(与历史 Pod 零差异)
    main = client._build_pod_body(
        "pod-1", _base_spec()).kwargs["spec"].kwargs["containers"][0].kwargs
    assert "security_context" not in main


def test_build_pod_body_node_name(client):
    """node_name → V1PodSpec.node_name(绕调度器点名绑节点);未给/空串归 None。"""
    pod = client._build_pod_body("pod-1", _base_spec(node_name="ecs-38b3-0001"))
    assert pod.kwargs["spec"].kwargs["node_name"] == "ecs-38b3-0001"
    pod = client._build_pod_body("pod-1", _base_spec())
    assert pod.kwargs["spec"].kwargs["node_name"] is None
    pod = client._build_pod_body("pod-1", _base_spec(node_name=""))
    assert pod.kwargs["spec"].kwargs["node_name"] is None


# -------------------------------------------------------------- PVC 同 claim 去重

def _spec_with_pvcs(client, main_pvc, sc_pvc):
    """主容器 + jiuwenbox sidecar 各带 pvc_mounts 的 spec(均走规范形校验)。"""
    from agent_runtime.mounts import validate_agent_mounts
    from agent_runtime.sidecars import validate_sidecars

    hp, cm, pvc = validate_agent_mounts([], [], main_pvc, nfs_mount_path="/nfs")
    sc = dict(JIUWENBOX, pvc_mounts=sc_pvc)
    sidecars = validate_sidecars([sc], container_name="agent",
                                 sse_port=8086, container_port=8086)
    return _base_spec(nfs_mount_path="/nfs", agent_pvc_mounts=pvc,
                      sidecars=sidecars)


def test_build_pod_body_pvc_same_claim_shared_across_containers(client):
    """同 claim PVC 跨主/sidecar:只建一个共享卷,sidecar 复用主容器卷名。"""
    spec = _spec_with_pvcs(
        client,
        [{"claim_name": "shared-data", "mount_path": "/data"}],
        [{"claim_name": "shared-data", "mount_path": "/var/lib/box"}])
    ps = client._build_pod_body("pod-1", spec).kwargs["spec"].kwargs
    pvc_vols = [v for v in ps["volumes"] if "persistent_volume_claim" in v.kwargs]
    assert len(pvc_vols) == 1                            # 去重:同 claim 一卷
    assert pvc_vols[0].kwargs["name"] == "pvc-agent-0-0"  # 首现=主容器(idx 0)
    assert pvc_vols[0].kwargs["persistent_volume_claim"].kwargs == {
        "claim_name": "shared-data", "read_only": False}
    # sidecar 不再有自己前缀的 pvc 卷,mount 引用主容器卷名
    assert "pvc-jiuwenbox-0-0" not in [v.kwargs["name"] for v in ps["volumes"]]
    main_mounts = {m.kwargs["mount_path"]: m.kwargs
                   for m in ps["containers"][0].kwargs["volume_mounts"]}
    box_mounts = {m.kwargs["mount_path"]: m.kwargs
                  for m in ps["containers"][1].kwargs["volume_mounts"]}
    assert main_mounts["/data"]["name"] == "pvc-agent-0-0"
    assert box_mounts["/var/lib/box"]["name"] == "pvc-agent-0-0"


def test_build_pod_body_pvc_different_claims_not_deduped(client):
    """异 claim 不误伤:各建各卷、各引用各卷名。"""
    spec = _spec_with_pvcs(
        client,
        [{"claim_name": "agent-data", "mount_path": "/data"}],
        [{"claim_name": "box-data", "mount_path": "/var/lib/box"}])
    ps = client._build_pod_body("pod-1", spec).kwargs["spec"].kwargs
    vols = {v.kwargs["name"]: v.kwargs for v in ps["volumes"]}
    assert set(vols) >= {"pvc-agent-0-0", "pvc-jiuwenbox-0-0"}
    box_mounts = {m.kwargs["mount_path"]: m.kwargs
                  for m in ps["containers"][1].kwargs["volume_mounts"]}
    assert box_mounts["/var/lib/box"]["name"] == "pvc-jiuwenbox-0-0"


def test_build_pod_body_pvc_shared_claim_read_only_first_wins(client):
    """卷级 read_only 取首现容器(主容器先渲染);mount 级保留各自值。

    kubelet 语义:卷源 readOnly=True 时该 claim 的所有挂载实际只读——
    sidecar 想要 rw 会被静默压成 ro。锁定现状(若日后语义收紧应改 400)。
    """
    spec = _spec_with_pvcs(
        client,
        [{"claim_name": "shared-data", "mount_path": "/data", "read_only": True}],
        [{"claim_name": "shared-data", "mount_path": "/var/lib/box",
          "read_only": False}])
    ps = client._build_pod_body("pod-1", spec).kwargs["spec"].kwargs
    pvc_vol = [v for v in ps["volumes"]
               if "persistent_volume_claim" in v.kwargs][0]
    assert pvc_vol.kwargs["persistent_volume_claim"].kwargs["read_only"] is True
    main_mounts = {m.kwargs["mount_path"]: m.kwargs
                   for m in ps["containers"][0].kwargs["volume_mounts"]}
    box_mounts = {m.kwargs["mount_path"]: m.kwargs
                  for m in ps["containers"][1].kwargs["volume_mounts"]}
    assert main_mounts["/data"]["read_only"] is True
    assert box_mounts["/var/lib/box"]["read_only"] is False  # mount 级原样
