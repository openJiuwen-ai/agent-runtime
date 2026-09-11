# coding: utf-8
"""containers 共享模块测试:统一 canonical 形/role 值域/跨容器校验/
normalize_pod_spec 指纹承重(2026-09 统一重构的新基线)。"""

from __future__ import annotations

import pytest

from agent_runtime.containers import (
    MAIN_PROBE_DEFAULT,
    SIDECAR_MAX,
    canonical_container,
    default_main_container,
    find_container_conflict,
    main_health_path,
    main_sse_port,
    normalize_container,
    normalize_containers,
    normalize_pod_spec,
    validate_pod_containers,
)
from agent_runtime.errors import InvalidParams
from agent_runtime.util import fingerprint

# jiuwenbox 全量样例(canonical 拼写;与旧 24 键值级对应)
JIUWENBOX = {
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "ports": [{"name": None, "container_port": 8321}],
    "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321",
            "JIUWENBOX_POLICY_PATH": "/app/configs/enterprise-policy.yaml"},
    "resources": {"cpu_request": "100m", "memory_request": "128Mi",
                  "cpu_limit": "1", "memory_limit": "1Gi"},
    "security_context": {"run_as_user": None, "run_as_group": None,
                         "privileged": True,
                         "capabilities_add": ["SYS_ADMIN", "NET_ADMIN"],
                         "capabilities_drop": [],
                         "seccomp_unconfined": True,
                         "apparmor_unconfined": True},
    "host_path_mounts": [
        {"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"}],
    "readiness_probe": {"probe_type": "tcp", "path": "/health",
                        "initial_delay": 10, "period": 5, "timeout": 3},
}

MAIN_FULL = {
    "name": "agent",
    "image": "agentserver:2.1",
    "image_pull_policy": "IfNotPresent",
    "command": ["/bin/agent", "--foreground"],
    "args": ["--port", "8086"],
    "ports": [{"name": "sse", "container_port": 8086},
              {"name": "http", "container_port": 9000}],
    "env": {"AGENT_HTTP_PORT": "8086"},
    "env_from": [{"prefix": "DB_",
                  "secret_ref": {"name": "agent-secret", "optional": False}}],
    "resources": {"cpu_request": "500m", "memory_request": "1Gi",
                  "cpu_limit": "2", "memory_limit": "4Gi"},
    "host_path_mounts": [],
    "configmap_mounts": [{"config_map_name": "agent-cm",
                          "mount_path": "/etc/agent/config.yaml",
                          "sub_path": "config.yaml", "items": None,
                          "read_only": True}],
    "pvc_mounts": [{"claim_name": "agent-data", "mount_path": "/var/lib/agent",
                    "read_only": False}],
    "nfs_mounts": [{"server": "10.0.0.1", "path": "/export",
                    "mount_path": "/mnt/nfs", "read_only": False}],
    "security_context": {"run_as_user": 1000, "run_as_group": 1000,
                         "privileged": False, "capabilities_add": [],
                         "capabilities_drop": [],
                         "seccomp_unconfined": False,
                         "apparmor_unconfined": False},
    "readiness_probe": {"probe_type": "http", "path": "/api/v1/health",
                        "initial_delay": 5, "period": 5, "timeout": None},
}


def _validate(main=None, sidecars=None, where="template 'tpl'"):
    return validate_pod_containers(
        MAIN_FULL if main is None else main, sidecars, where)


def _ver(spec: dict) -> str:
    norm = normalize_pod_spec(spec)
    return fingerprint({k: norm[k] for k in ("main_container", "sidecars")})


def _spec(main=None, sidecars=None) -> dict:
    return {"main_container": MAIN_FULL if main is None else main,
            "sidecars": sidecars}


# -------------------------------------------------------------- canonical 规范形

def test_canonical_main_full_fields_roundtrip():
    """全量主容器 → canonical:13 键、显式值全保留(幂等输入)。"""
    out = canonical_container(MAIN_FULL, "main", role="main")
    assert set(out) == set(MAIN_FULL)
    assert out == MAIN_FULL


def test_canonical_defaults_filled():
    """缺省输入 → 默认键全填满(同指纹的前提:「显式默认」==「省略」)。"""
    out = canonical_container({"image": "x:1"}, "main", role="main")
    assert out["name"] == "agent"
    assert out["image_pull_policy"] == "IfNotPresent"
    assert out["command"] is None and out["args"] is None
    assert out["ports"] == [{"name": "sse", "container_port": 8080}]
    assert out["env"] == {} and out["env_from"] is None
    assert out["resources"] == {"cpu_request": None, "memory_request": None,
                                "cpu_limit": None, "memory_limit": None}
    assert out["host_path_mounts"] == [] and out["pvc_mounts"] == []
    assert out["nfs_mounts"] == []
    assert out["security_context"]["privileged"] is False
    assert out["readiness_probe"] == dict(MAIN_PROBE_DEFAULT)

    sc = canonical_container({"name": "box", "image": "x:1"}, "sc", role="sidecar")
    assert sc["ports"] is None
    assert sc["readiness_probe"] == {"probe_type": None, "path": "/health",
                                     "initial_delay": 5, "period": 10,
                                     "timeout": 3}


def test_canonical_is_idempotent():
    """canonical(canonical(x)) == canonical(x)(无等价异形)。"""
    for role, item in (("main", MAIN_FULL), ("sidecar", JIUWENBOX),
                       ("main", {"image": "x:1"}),
                       ("sidecar", {"name": "b", "image": "x:1"})):
        once = canonical_container(item, "w", role=role)
        assert canonical_container(once, "w", role=role) == once


def test_canonical_env_from_is_full_key():
    """env_from 全键携带(值可为 None)——条件键机制废除的固化。"""
    out = canonical_container({"image": "x:1"}, "main", role="main")
    assert "env_from" in out and out["env_from"] is None
    out = canonical_container(
        {"image": "x:1", "env_from": []}, "main", role="main")
    assert out["env_from"] is None
    out = canonical_container(
        {"name": "b", "image": "x:1",
         "env_from": [{"secret_ref": {"name": "s"}}]}, "sc", role="sidecar")
    assert out["env_from"] == [
        {"prefix": None, "secret_ref": {"name": "s", "optional": False}}]


def test_canonical_caps_sorted_and_deduped():
    """capabilities 排序 + 去重(K8s 无序语义;序变不扰动指纹)。"""
    out = canonical_container(
        {"name": "b", "image": "x:1",
         "security_context": {"capabilities_add": ["NET_ADMIN", "SYS_ADMIN",
                                                   "NET_ADMIN"]}},
        "sc", role="sidecar")
    assert out["security_context"]["capabilities_add"] == [
        "NET_ADMIN", "SYS_ADMIN"]


def test_canonical_probe_path_gets_leading_slash():
    out = canonical_container(
        {"image": "x:1", "readiness_probe": {"path": "api/v1/health"}},
        "main", role="main")
    assert out["readiness_probe"]["path"] == "/api/v1/health"


# -------------------------------------------------------------- role 值域(拒绝矩阵)

@pytest.mark.parametrize("secctx_extra", [
    {"privileged": True},
    {"capabilities_add": ["SYS_ADMIN"]},
    {"capabilities_drop": ["ALL"]},
    {"seccomp_unconfined": True},
    {"apparmor_unconfined": True},
])
def test_main_rejects_sidecar_only_security(secctx_extra):
    with pytest.raises(InvalidParams, match="sidecar-only"):
        canonical_container(
            {"image": "x:1", "security_context": secctx_extra},
            "main", role="main")


@pytest.mark.parametrize("probe", [
    {"probe_type": "tcp"},
    {"probe_type": None},
    {"timeout": 3},
])
def test_main_rejects_non_http_probe_or_timeout(probe):
    with pytest.raises(InvalidParams):
        canonical_container(
            {"image": "x:1", "readiness_probe": probe}, "main", role="main")


@pytest.mark.parametrize("ports", [
    [{"name": "http", "container_port": 9000}],                    # 缺 sse
    [{"name": "sse", "container_port": 8086},
     {"name": "sse", "container_port": 8087}],                     # 双 sse
    [{"name": "sse", "container_port": 8086},
     {"name": "http", "container_port": 9000},
     {"name": "http", "container_port": 9001}],                    # 双 http
    [{"name": "metrics", "container_port": 9090}],                 # 非法名
])
def test_main_ports_contract(ports):
    with pytest.raises(InvalidParams):
        canonical_container({"image": "x:1", "ports": ports},
                            "main", role="main")


def test_main_ports_fixed_order_sse_first():
    out = canonical_container(
        {"image": "x:1", "ports": [{"name": "http", "container_port": 9000},
                                   {"name": "sse", "container_port": 8086}]},
        "main", role="main")
    assert [p["name"] for p in out["ports"]] == ["sse", "http"]


@pytest.mark.parametrize("ports", [
    [{"name": "box", "container_port": 8321}],      # sidecar 必须无名端口
    [{"container_port": 8321}, {"container_port": 8322}],  # 至多一个
])
def test_sidecar_rejects_named_or_multiple_ports(ports):
    with pytest.raises(InvalidParams):
        canonical_container({"name": "b", "image": "x:1", "ports": ports},
                            "sc", role="sidecar")


def test_sidecar_nfs_mounts_allowed():
    """sidecar 可挂 NFS(上游 bef82fc4 起,第四挂载族对主/sidecar 一致开放)。"""
    out = canonical_container(
        {"name": "b", "image": "x:1",
         "nfs_mounts": [{"server": "s", "mount_path": "/m"}]},
        "sc", role="sidecar")
    assert out["nfs_mounts"] == [{"server": "s", "path": None,
                                  "mount_path": "/m", "read_only": False}]


def test_command_args_validation():
    """command/args:None/[] 同义 None;非字符串项 400。"""
    out = canonical_container(
        {"image": "x:1", "command": [], "args": None}, "main", role="main")
    assert out["command"] is None and out["args"] is None
    with pytest.raises(InvalidParams, match="command"):
        canonical_container({"image": "x:1", "command": [42]}, "main",
                            role="main")
    with pytest.raises(InvalidParams, match=r"args"):
        canonical_container({"image": "x:1", "args": "x"}, "main",
                            role="main")


def test_sidecar_probe_requires_ports():
    with pytest.raises(InvalidParams, match="requires ports"):
        canonical_container(
            {"name": "b", "image": "x:1",
             "readiness_probe": {"probe_type": "tcp"}},
            "sc", role="sidecar")


def test_canonical_rejects_unknown_keys():
    with pytest.raises(InvalidParams, match=r"unknown keys.*capabilites_add"):
        canonical_container(
            {"name": "b", "image": "x:1", "capabilites_add": ["X"]},
            "sc", role="sidecar")


@pytest.mark.parametrize("item", [
    {"image": "x:1"},               # sidecar 缺 name
    {"name": "b"},                  # 缺 image
    {"name": "Jiuwen_Box", "image": "x:1"},  # 非 DNS-1123
])
def test_canonical_rejects_bad_name_or_image(item):
    with pytest.raises(InvalidParams):
        canonical_container(item, "sc", role="sidecar")


# -------------------------------------------------------------- validate_pod_containers

def test_validate_pod_containers_returns_sorted_canonical_pair():
    main, sidecars = _validate(sidecars=[JIUWENBOX,
                                         {"name": "a-box", "image": "x:1"}])
    assert main == MAIN_FULL
    assert [sc["name"] for sc in sidecars] == ["a-box", "jiuwenbox"]
    assert _validate(sidecars=None)[1] is None


def test_validate_rejects_oversized_sidecars():
    items = [{"name": f"box-{i}", "image": "x:1"}
             for i in range(SIDECAR_MAX + 1)]
    with pytest.raises(InvalidParams, match="at most"):
        _validate(sidecars=items)


def test_validate_rejects_duplicate_sidecar_names():
    with pytest.raises(InvalidParams, match="duplicate container names"):
        _validate(sidecars=[{"name": "box", "image": "x:1"},
                            {"name": "box", "image": "y:2"}])


@pytest.mark.parametrize("main_ports,sc_ports,match", [
    ([{"name": "sse", "container_port": 8086}],
     [{"container_port": 8086}], "conflicts with the main"),
    ([{"name": "sse", "container_port": 8086},
      {"name": "http", "container_port": 9000}],
     [{"container_port": 9000}], "conflicts with the main"),
    ([{"name": "sse", "container_port": 8086}],
     [{"container_port": 8321}], None),
])
def test_validate_port_conflicts(main_ports, sc_ports, match):
    main = dict(MAIN_FULL, ports=main_ports)
    if match is None:
        _validate(main=main,
                  sidecars=[{"name": "b", "image": "x:1",
                             "ports": sc_ports}])
        return
    with pytest.raises(InvalidParams, match=match):
        _validate(main=main,
                  sidecars=[{"name": "b", "image": "x:1",
                             "ports": sc_ports}])


def test_validate_rejects_sibling_port_conflict():
    with pytest.raises(InvalidParams, match="differ from each other"):
        _validate(sidecars=[{"name": "a", "image": "x:1",
                             "ports": [{"container_port": 8321}]},
                            {"name": "b", "image": "x:1",
                             "ports": [{"container_port": 8321}]}])


def test_validate_rejects_sidecar_name_equals_main():
    with pytest.raises(InvalidParams, match="conflicts with the main"):
        _validate(sidecars=[{"name": "agent", "image": "x:1"}])


def test_validate_rejects_main_mount_path_conflicts_incl_nfs():
    main = dict(MAIN_FULL,
                host_path_mounts=[{"host_path": "/h",
                                   "mount_path": "/mnt/nfs"}])
    with pytest.raises(InvalidParams, match="mount_path"):
        _validate(main=main)
    main2 = dict(MAIN_FULL,
                 configmap_mounts=[{"config_map_name": "cm",
                                    "mount_path": "/var/lib/agent"}])
    with pytest.raises(InvalidParams, match="mount_path"):
        _validate(main=main2)


def test_find_container_conflict_is_pure_predicate():
    main, sidecars = _validate(sidecars=[JIUWENBOX])
    assert find_container_conflict(main, sidecars) is None
    clash = dict(main, name="jiuwenbox")
    assert find_container_conflict(clash, sidecars) is not None
    port_clash = [dict(sc, ports=[{"name": None,
                                   "container_port": 8086}])
                  for sc in sidecars]
    assert find_container_conflict(main, port_clash) is not None


# -------------------------------------------------------------- normalize(宽容)

def test_normalize_container_main_fallbacks():
    default = default_main_container()
    assert normalize_container(None, role="main") == default
    assert normalize_container("garbage", role="main") == default
    assert normalize_container({"image": ""}, role="main") == default
    assert normalize_container({}, role="main") == default
    # 合法值照常 canonical
    assert normalize_container(MAIN_FULL, role="main") == MAIN_FULL


def test_normalize_container_sidecar_drops_bad():
    assert normalize_container(None, role="sidecar") is None
    assert normalize_container({"name": "b"}, role="sidecar") is None
    assert normalize_container(
        {"name": "b", "image": "x:1"}, role="sidecar") == canonical_container(
        {"name": "b", "image": "x:1"}, "sidecars[n]", role="sidecar")


@pytest.mark.parametrize("value,expected_names", [
    (None, None),
    ([], None),
    ("garbage", None),
    ([None, "x", 42], None),
    ([{"garbage": 1}, {"name": "b", "image": "x:1"},
      {"name": "a", "image": "x:1"}], ["a", "b"]),  # 坏项丢弃 + name 排序
])
def test_normalize_containers_tolerates_corrupt_input(value, expected_names):
    out = normalize_containers(value)
    if expected_names is None:
        assert out is None
    else:
        assert [sc["name"] for sc in out] == expected_names


def test_default_main_container_shape():
    """缺省主容器 = 13 键全满 + 空镜像哨兵(旧 agent_image='' 同语义)。"""
    default = default_main_container()
    assert set(default) == set(canonical_container(
        {"name": "x", "image": "y"}, "w", role="main"))
    assert default["image"] == ""
    assert default["name"] == "agent"
    assert default["ports"] == [{"name": "sse", "container_port": 8080}]


# -------------------------------------------------------------- 派生 helper

def test_main_sse_port_and_health_path_helpers():
    assert main_sse_port(MAIN_FULL) == 8086
    assert main_health_path(MAIN_FULL) == "/api/v1/health"
    # 缺省/坏值兜底
    assert main_sse_port(None) == 8080
    assert main_sse_port({}) == 8080
    assert main_health_path(None) == "/health"
    assert main_health_path({}) == "/health"


# -------------------------------------------------------------- normalize_pod_spec(指纹承重)

def test_normalize_pod_spec_fills_missing_keys_without_changing_values():
    """旧缓存缺键(未来加字段的存量 pod_spec_json)→ 只补缺省,不改已有值。

    承重:新键默认值 == 旧行为时,旧缓存正规化后与新算指纹相等 → 零伪日落。
    """
    minimal = {"main_container": {"name": "agent", "image": "img:1"},
               "sidecars": [{"name": "b", "image": "y:1"}]}
    full_form = {"main_container": canonical_container(
                     {"name": "agent", "image": "img:1"}, "w", role="main"),
                 "sidecars": [canonical_container(
                     {"name": "b", "image": "y:1"}, "w", role="sidecar")]}
    assert _ver(minimal) == _ver(full_form)
    # 模拟"未来加字段后"的旧缓存:从全键形态里删默认值键(段落级/嵌套级)
    trimmed_main = {k: v for k, v in full_form["main_container"].items()
                    if k not in ("ports", "resources", "readiness_probe")}
    trimmed_sc = {k: v for k, v in full_form["sidecars"][0].items()
                  if k != "security_context"}
    legacy = {"main_container": trimmed_main, "sidecars": [trimmed_sc]}
    assert _ver(legacy) == _ver(full_form)


def test_normalize_pod_spec_different_values_differ():
    """合法不同值 → 指纹必不等(防过度归一造伪相等 → 旧 Pod 误复用)。"""
    assert _ver(_spec()) != _ver(
        _spec(main=dict(MAIN_FULL, image="agentserver:2.2")))
    assert _ver(_spec()) != _ver(
        _spec(sidecars=[{"name": "b", "image": "x:1"}]))
    assert _ver(_spec()) != _ver(
        _spec(main=dict(MAIN_FULL, readiness_probe=dict(
            MAIN_PROBE_DEFAULT, period=7))))


def test_normalize_pod_spec_key_and_list_order_stable():
    """键序乱序 + sidecar 列表乱序 → 同指纹(canonical 序 = name 升序)。"""
    rev_main = {k: MAIN_FULL[k] for k in reversed(list(MAIN_FULL))}
    rev_sc = {k: JIUWENBOX[k] for k in reversed(list(JIUWENBOX))}
    assert _ver(_spec(main=rev_main,
                      sidecars=[JIUWENBOX, {"name": "a-box", "image": "x:1"}])) \
        == _ver(_spec(sidecars=[{"name": "a-box", "image": "x:1"}, rev_sc]))


def test_normalize_pod_spec_tolerates_garbage_and_keeps_unknown_keys():
    """坏容器段不炸(渲染层另行 shape 探测);未知键保留(指纹可见,不静默吞)。"""
    out = normalize_pod_spec({"main_container": "garbage",
                              "sidecars": [{"name": "b"}, "x"],
                              "namespace": "default"})
    assert out["main_container"] == "garbage"          # 非 dict 原样(渲染层跳过)
    assert out["namespace"] == "default"               # 模板级透传
    out = normalize_pod_spec({"main_container": {
        "name": "agent", "image": "x:1", "future_key": 1}})
    assert out["main_container"]["future_key"] == 1    # 未知键不丢
    # 坏 sidecar 项(缺 image)被丢弃——渲染无镜像容器没有意义;合法项补满
    out = normalize_pod_spec({"sidecars": [{"name": "b", "image": "y"},
                                           {"image": "y"}]})
    assert [sc["name"] for sc in out["sidecars"]] == ["b"]
    assert out["sidecars"][0]["readiness_probe"]["period"] == 10  # 默认补齐
