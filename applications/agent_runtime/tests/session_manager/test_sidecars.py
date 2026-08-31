# coding: utf-8
"""sidecars 共享模块测试:规范形/校验拒绝矩阵/指纹不变式(承重兼容断言)。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import InvalidParams
from agent_runtime.session_manager.models import Template
from agent_runtime.sidecars import (
    SIDECAR_MAX,
    find_sidecar_conflict,
    normalize_sidecars,
    validate_sidecars,
)
from agent_runtime.spec_fields import DEPLOY_VER_FIELDS
from agent_runtime.util import fingerprint

# jiuwenbox 全量样例(与文档/feature 记录共用形态)
JIUWENBOX = {
    "name": "jiuwenbox",
    "image": "jiuwenbox-amd64:0.0.1",
    "port": 8321,
    "env": {"JIUWENBOX_LISTEN": "tcp://0.0.0.0:8321",
            "JIUWENBOX_POLICY_PATH": "/app/configs/enterprise-policy.yaml"},
    "cpu_request": "100m",
    "memory_request": "128Mi",
    "cpu_limit": "1",
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


def _validate(value, **kw):
    kw.setdefault("container_name", "agent")
    kw.setdefault("sse_port", 8080)
    kw.setdefault("container_port", 8080)
    return validate_sidecars(value, **kw)


# -------------------------------------------------------------- 指纹不变式(承重)

def test_sidecars_none_keeps_deploy_ver_byte_identical():
    """无 sidecar:新字段集指纹 == 旧字段集(不含 sidecars 键)指纹,逐字节相等。

    这是存量模板/暖 Pod 不被全量日落的承重断言(fingerprint 只滤 None)。
    """
    t = Template(template_id="tpl", agent_image="img:1", agent_env={"A": "1"})
    full = {f: getattr(t, f) for f in DEPLOY_VER_FIELDS}
    legacy = {f: getattr(t, f) for f in DEPLOY_VER_FIELDS if f != "sidecars"}
    assert t.sidecars is None
    assert fingerprint(full) == fingerprint(legacy)
    # 无 sidecar 的 deploy_subset 整体 json 化后与"旧字段集+None"一致(键存在值为 None)
    subset = t.deploy_subset()
    assert "sidecars" in subset and subset["sidecars"] is None


def test_sidecars_empty_list_normalized_to_none():
    """[] → None:与"不下发 sidecars"同指纹、同 dataclass 相等。"""
    t_empty = Template(template_id="tpl", agent_image="img:1", sidecars=[])
    t_none = Template(template_id="tpl", agent_image="img:1")
    assert t_empty.sidecars is None
    assert t_empty == t_none
    assert t_empty.deploy_ver() == t_none.deploy_ver()


def test_sidecars_order_and_key_order_do_not_change_fingerprint():
    """键序乱序 + env 键序乱序 + 列表顺序重排 → 同一 deploy_ver(缺陷④回归网)。"""
    sc_a = dict(JIUWENBOX)
    sc_b = {"name": "logtail", "image": "logtail:1", "port": 9090}
    t1 = Template(template_id="tpl", agent_image="img:1",
                  sidecars=[sc_a, sc_b])
    # 乱序键 + env 键倒序 + 列表倒序(重建 dict 保证键插入序不同)
    sc_a_rev = {k: sc_a[k] for k in reversed(list(sc_a))}
    sc_a_rev["env"] = dict(reversed(list(sc_a["env"].items())))
    t2 = Template(template_id="tpl", agent_image="img:1",
                  sidecars=[sc_b, sc_a_rev])
    assert t1.deploy_ver() == t2.deploy_ver()


def test_sidecars_change_changes_deploy_ver():
    """sidecar 内容变更(如 env)→ 指纹变(A 类日落语义)。"""
    t1 = Template(template_id="tpl", agent_image="img:1", sidecars=[JIUWENBOX])
    sc2 = {**JIUWENBOX, "image": "jiuwenbox-amd64:0.0.2"}
    t2 = Template(template_id="tpl", agent_image="img:1", sidecars=[sc2])
    assert t1.deploy_ver() != t2.deploy_ver()


# -------------------------------------------------------------- 规范形

def test_validate_sidecars_canonicalizes_jiuwenbox_spec():
    """全量样例 → 规范形:默认键填满、env str 化、按 name 排序。"""
    out = _validate([JIUWENBOX, {"name": "a-box", "image": "x:1"}])
    assert [sc["name"] for sc in out] == ["a-box", "jiuwenbox"]  # name 升序
    box = out[1]
    # 显式值保留
    assert box["port"] == 8321 and box["privileged"] is True
    assert box["capabilities_add"] == ["SYS_ADMIN", "NET_ADMIN"]
    assert box["host_path_mounts"] == [
        {"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup",
         "read_only": False, "host_path_type": None},
    ]
    # 缺省键填满默认值
    assert box["image_pull_policy"] == "IfNotPresent"
    assert box["capabilities_drop"] == []
    assert box["run_as_user"] is None and box["run_as_group"] is None
    assert box["readiness_path"] == "/health"
    assert box["readiness_initial_delay"] == 10 and box["readiness_period"] == 5
    assert box["readiness_timeout_seconds"] == 3
    # 最小项全默认
    assert out[0]["privileged"] is False and out[0]["env"] == {}
    assert out[0]["port"] is None
    assert out[0]["readiness_initial_delay"] == 5
    assert out[0]["readiness_period"] == 10
    assert out[0]["readiness_timeout_seconds"] == 3


def test_validate_sidecars_explicit_defaults_equal_omitted():
    """「显式给默认值」与「省略键」→ 同一规范形(同指纹的前提)。"""
    omitted = _validate([{"name": "box", "image": "x:1"}])
    explicit = _validate([{"name": "box", "image": "x:1", "port": None,
                           "env": {}, "image_pull_policy": "IfNotPresent",
                           "privileged": False, "capabilities_add": [],
                           "capabilities_drop": [], "seccomp_unconfined": False,
                           "apparmor_unconfined": False, "run_as_user": None,
                           "run_as_group": None, "host_path_mounts": [],
                           "readiness_probe_type": None,
                           "readiness_path": "/health",
                           "readiness_initial_delay": 5,
                           "readiness_period": 10,
                           "readiness_timeout_seconds": 3}])
    assert omitted == explicit
    assert _validate(None) is None
    assert _validate([]) is None  # 空列表归一为 None


# -------------------------------------------------------------- 拒绝矩阵

@pytest.mark.parametrize("item", [
    {"image": "x:1"},                                   # 缺 name
    {"name": "box"},                                    # 缺 image
    {"name": "", "image": "x:1"},                       # 空 name
    {"name": "box", "image": ""},                       # 空 image
])
def test_validate_sidecars_rejects_missing_name_or_image(item):
    with pytest.raises(InvalidParams, match=r"sidecars\[0\]"):
        _validate([item])


@pytest.mark.parametrize("bad_name", [
    "Jiuwen_Box",   # 大写/下划线
    "-box",         # 首字符 '-'
    "box-",         # 尾字符 '-'
    "a" * 64,       # 超 63
])
def test_validate_sidecars_rejects_invalid_name(bad_name):
    with pytest.raises(InvalidParams, match="DNS-1123"):
        _validate([{"name": bad_name, "image": "x:1"}])


def test_validate_sidecars_rejects_duplicate_names():
    with pytest.raises(InvalidParams, match="duplicate container names"):
        _validate([{"name": "box", "image": "x:1"},
                   {"name": "box", "image": "y:2"}])


def test_validate_sidecars_rejects_agent_container_name_collision():
    with pytest.raises(InvalidParams, match="conflicts with the agent container_name"):
        _validate([{"name": "agent", "image": "x:1"}], container_name="agent")


def test_validate_sidecars_rejects_unknown_keys():
    with pytest.raises(InvalidParams, match="unknown keys.*capabilites_add"):
        _validate([{"name": "box", "image": "x:1",
                    "capabilites_add": ["SYS_ADMIN"]}])  # 拼写错误必须 400


def test_validate_sidecars_rejects_bad_env():
    with pytest.raises(InvalidParams, match=r"sidecars\[0\]\.env"):
        _validate([{"name": "box", "image": "x:1",
                    "env": {"K": ["v"]}}])


def test_validate_sidecars_rejects_probe_without_port():
    with pytest.raises(InvalidParams, match="requires port"):
        _validate([{"name": "box", "image": "x:1",
                    "readiness_probe_type": "tcp"}])


def test_validate_sidecars_rejects_bad_probe_type():
    with pytest.raises(InvalidParams, match="readiness_probe_type"):
        _validate([{"name": "box", "image": "x:1", "port": 9000,
                    "readiness_probe_type": "grpc"}])


@pytest.mark.parametrize("port,match", [
    (8080, "conflicts with the agent container ports"),   # 撞 sse_port
    (8081, "conflicts with the agent container ports"),   # 撞 container_port
    (9000, "conflicts with sidecars"),                    # 撞兄弟 sidecar
])
def test_validate_sidecars_rejects_port_conflicts(port, match):
    base = dict(sse_port=8080, container_port=8081)
    with pytest.raises(InvalidParams, match=match):
        if port == 9000:
            _validate([{"name": "a", "image": "x:1", "port": 9000},
                       {"name": "b", "image": "x:1", "port": 9000}], **base)
        else:
            _validate([{"name": "box", "image": "x:1", "port": port}], **base)


@pytest.mark.parametrize("mount", [
    {"host_path": "sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"},  # 相对 host_path
    {"host_path": "/sys/fs/cgroup"},                                 # 缺 mount_path
    {"host_path": "/a", "mount_path": "/b", "ro": True},             # 未知键
    {"host_path": "/a", "mount_path": "/b", "host_path_type": "Dir"},  # 坏类型枚举
])
def test_validate_sidecars_rejects_bad_host_path_mounts(mount):
    with pytest.raises(InvalidParams, match=r"host_path_mounts\[0\]"):
        _validate([{"name": "box", "image": "x:1",
                    "host_path_mounts": [mount]}])


def test_validate_sidecars_rejects_oversized_list():
    items = [{"name": f"box-{i}", "image": "x:1"} for i in range(SIDECAR_MAX + 1)]
    with pytest.raises(InvalidParams, match="at most"):
        _validate(items)


def test_validate_sidecars_rejects_non_list():
    with pytest.raises(InvalidParams, match="must be a list"):
        _validate({"name": "box"})


def test_validate_sidecars_rejects_bad_port_range():
    with pytest.raises(InvalidParams, match=r"port"):
        _validate([{"name": "box", "image": "x:1", "port": 70000}])


# -------------------------------------------------------------- normalize(宽容)

@pytest.mark.parametrize("value,expected", [
    (None, None),
    ([], None),
    ("garbage", None),
    ({}, None),
    ([None, "x", 42], None),                       # 全坏项 → 空 → None
    ([{"garbage": 1}, {"name": "box", "image": "x:1"}],
     [{"name": "box", "image": "x:1"}]),           # 坏项丢弃,合法项保留
])
def test_normalize_sidecars_tolerates_corrupt_input(value, expected):
    out = normalize_sidecars(value)
    if expected is None:
        assert out is None
    else:
        assert out is not None
        # 宽容路径产物同样是规范形(默认键填满)
        assert out == _validate(expected)


def test_find_sidecar_conflict_is_pure_predicate():
    """纯谓词:冲突返回描述串,无冲突返回 None(RM 侧包 DeployFailed 用)。"""
    sc = validate_sidecars([JIUWENBOX], container_name="agent",
                           sse_port=8080, container_port=8080)
    assert sc is not None
    assert find_sidecar_conflict(sc, "agent", 8080, 8080) is None
    assert find_sidecar_conflict(sc, "agent", 8321, 8080) is not None
    assert find_sidecar_conflict(sc, "jiuwenbox", 8080, 8080) is not None


# -------------------------------------------------------------- sidecar 挂载

def test_sidecar_configmap_and_pvc_mounts_canonical():
    """sidecar 三种挂载:cm/pvc 缺省 []、显式值规范化 + mount_path 冲突拒绝。"""
    out = _validate([{
        "name": "box", "image": "x:1",
        "configmap_mounts": [{"config_map_name": "cm", "mount_path": "/cfg"}],
        "pvc_mounts": [{"claim_name": "p", "mount_path": "/vol", "read_only": True}],
    }])
    box = out[0]
    assert box["host_path_mounts"] == []          # 缺省空列表
    assert box["configmap_mounts"] == [{"config_map_name": "cm",
                                        "mount_path": "/cfg", "sub_path": None,
                                        "items": None, "read_only": True}]
    assert box["pvc_mounts"] == [{"claim_name": "p", "mount_path": "/vol",
                                  "read_only": True}]


def test_sidecar_rejects_duplicate_mount_path_across_kinds():
    """同一 sidecar 内 hostPath 与 ConfigMap 挂到同一路径 → 400(K8s 会拒)。"""
    with pytest.raises(InvalidParams, match="mount_path.*duplicated"):
        _validate([{
            "name": "box", "image": "x:1",
            "host_path_mounts": [{"host_path": "/h", "mount_path": "/cfg"}],
            "configmap_mounts": [{"config_map_name": "cm", "mount_path": "/cfg"}],
        }])


def test_sidecar_rejects_bad_configmap_mount():
    with pytest.raises(InvalidParams, match=r"configmap_mounts\[0\]"):
        _validate([{"name": "box", "image": "x:1",
                    "configmap_mounts": [{"config_map_name": "Bad!",
                                          "mount_path": "/cfg"}]}])


# -------------------------------------------------------------- envFrom(引用注入)

def test_env_from_canonical_matrix():
    from agent_runtime.sidecars import canonical_env_from
    # 合法形态 → 规范形(prefix 恒存、ref 填满 name/optional)
    assert canonical_env_from(None, "e") is None
    assert canonical_env_from([], "e") is None
    assert canonical_env_from(
        [{"secret_ref": {"name": "agent-secret"}}], "e") == [
            {"prefix": None, "secret_ref": {"name": "agent-secret", "optional": False}}]
    assert canonical_env_from(
        [{"prefix": "DB_", "config_map_ref": {"name": "cm-1", "optional": True}}], "e") == [
            {"prefix": "DB_", "config_map_ref": {"name": "cm-1", "optional": True}}]


@pytest.mark.parametrize("value,match", [
    ("x", r"list"),
    ([42], r"object"),
    ([{}], r"exactly one"),
    ([{"secret_ref": {"name": "s"}, "config_map_ref": {"name": "c"}}], r"exactly one"),
    ([{"prefix": "P", "unknown": 1}], r"unknown keys"),
    ([{"secret_ref": "s"}], r"object"),
    ([{"secret_ref": {}}], r"resource name"),
    ([{"secret_ref": {"name": "Bad_Name"}}], r"resource name"),
    ([{"secret_ref": {"name": "s", "extra": 1}}], r"unknown keys"),
    ([{"secret_ref": {"name": "s", "optional": "yes"}}], r"boolean"),
    ([{"prefix": "", "secret_ref": {"name": "s"}}], r"prefix"),
    ([{"prefix": "1A", "secret_ref": {"name": "s"}}], r"prefix"),
    ([{"prefix": 5, "secret_ref": {"name": "s"}}], r"prefix"),
])
def test_env_from_rejections(value, match):
    from agent_runtime.sidecars import canonical_env_from
    with pytest.raises(InvalidParams, match=match):
        canonical_env_from(value, "env_from")


def test_sidecar_env_from_is_conditional_key():
    """env_from 有值才出现(None/[] 省略键)——存量 sidecar 指纹零扰动的机制固化。"""
    base = {"name": "box", "image": "x:1"}
    for absent in (None, []):
        sc = _validate([dict(base, env_from=absent)])
        assert "env_from" not in sc[0]
    sc = _validate([dict(base, env_from=[{"secret_ref": {"name": "s"}}])])
    assert sc[0]["env_from"] == [
        {"prefix": None, "secret_ref": {"name": "s", "optional": False}}]


def test_env_from_absent_keeps_deploy_ver_byte_identical():
    """envFrom 缺省:deploy_ver 与增补 agent_env_from 字段前逐字节相等。

    常量为 2026-08-31 增补 envFrom 前的实测值(红线直接证据):
    存量模板(主容器缺省/sidecar 规范形/env+镜像组合)不被伪 A 类日落。
    """
    assert Template(template_id="x").deploy_ver() == "026cfcf9a6b31721"
    sc = _validate([{"name": "jiuwenbox", "image": "box:1", "port": 8321,
                     "env": {"A": "b"},
                     "host_path_mounts": [{"host_path": "/h", "mount_path": "/m"}]}])
    assert Template(template_id="x", sidecars=sc).deploy_ver() == "0e250e15b2040867"
    assert Template(template_id="x", agent_env={"K": "v"},
                    agent_image="img:1").deploy_ver() == "6670556a43eff884"
    # [] 归一 None → 与缺省同指纹
    assert (Template(template_id="x", agent_env_from=[])
            .deploy_ver() == Template(template_id="x").deploy_ver())


def test_env_from_changes_deploy_ver():
    """带 envFrom → 指纹变化 = 正确的 A 类日落(env 烘焙进 Pod)。"""
    plain = Template(template_id="x")
    main = Template(template_id="x", agent_env_from=[
        {"config_map_ref": {"name": "cm-1"}}])
    assert main.deploy_ver() != plain.deploy_ver()

    sc_plain = _validate([{"name": "box", "image": "x:1"}])
    sc_env = _validate([{"name": "box", "image": "x:1",
                         "env_from": [{"secret_ref": {"name": "s"}}]}])
    assert (Template(template_id="x", sidecars=sc_env).deploy_ver()
            != Template(template_id="x", sidecars=sc_plain).deploy_ver())
