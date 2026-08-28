# coding: utf-8
"""mounts 共享模块测试:三种挂载规范形/校验拒绝矩阵/指纹不变式/冲突检测。"""

from __future__ import annotations

import pytest

from agent_runtime.errors import InvalidParams
from agent_runtime.mounts import (
    canonical_configmap_mounts,
    canonical_host_path_mounts,
    canonical_pvc_mounts,
    find_mount_path_conflicts,
    normalize_mounts,
    validate_agent_mounts,
)
from agent_runtime.session_manager.models import Template
from agent_runtime.util import fingerprint

# -------------------------------------------------------------- 规范形

def test_host_path_canonical_form():
    out = canonical_host_path_mounts(
        [{"host_path": "/sys/fs/cgroup", "mount_path": "/sys/fs/cgroup"}],
        "agent_host_path_mounts")
    assert out == [{"host_path": "/sys/fs/cgroup",
                    "mount_path": "/sys/fs/cgroup",
                    "read_only": False, "host_path_type": None}]


def test_configmap_canonical_form_and_defaults():
    out = canonical_configmap_mounts(
        [{"config_map_name": "box-cm", "mount_path": "/etc/box"}],
        "agent_configmap_mounts")
    # ConfigMap 默认只读(沿老 SDK ConfigMapMount 语义);sub_path/items 缺省 None
    assert out == [{"config_map_name": "box-cm", "mount_path": "/etc/box",
                    "sub_path": None, "items": None, "read_only": True}]
    out2 = canonical_configmap_mounts(
        [{"config_map_name": "box-cm", "mount_path": "/etc/box/policy.yaml",
          "sub_path": "policy.yaml", "read_only": False,
          "items": [{"path": "p.yaml", "key": "k2"}, {"path": "a.yaml", "key": "k1"}]}],
        "agent_configmap_mounts")
    assert out2[0]["sub_path"] == "policy.yaml"
    assert out2[0]["read_only"] is False
    assert out2[0]["items"] == [{"key": "k1", "path": "a.yaml"},
                                {"key": "k2", "path": "p.yaml"}]  # 按 key 排序


def test_pvc_canonical_form():
    out = canonical_pvc_mounts([{"claim_name": "data-pvc", "mount_path": "/data"}],
                               "agent_pvc_mounts")
    assert out == [{"claim_name": "data-pvc", "mount_path": "/data",
                    "read_only": False}]  # PVC 默认可写


def test_mounts_sorted_by_mount_path():
    """列表按下发顺序无关(按 mount_path 升序)——挂载顺序无语义。"""
    out = canonical_pvc_mounts(
        [{"claim_name": "b", "mount_path": "/b"}, {"claim_name": "a", "mount_path": "/a"}],
        "agent_pvc_mounts")
    assert [m["mount_path"] for m in out] == ["/a", "/b"]


def test_explicit_defaults_equal_omitted():
    omitted = canonical_host_path_mounts(
        [{"host_path": "/h", "mount_path": "/m"}], "w")
    explicit = canonical_host_path_mounts(
        [{"host_path": "/h", "mount_path": "/m", "read_only": False,
          "host_path_type": None}], "w")
    assert omitted == explicit


# -------------------------------------------------------------- 拒绝矩阵

@pytest.mark.parametrize("kind,item,match", [
    ("host_path_mounts", {"host_path": "rel/path", "mount_path": "/m"}, "absolute host_path"),
    ("host_path_mounts", {"host_path": "/h", "mount_path": "m"}, "absolute mount_path"),
    ("host_path_mounts", {"host_path": "/h", "mount_path": "/m", "ro": True}, "unknown keys"),
    ("host_path_mounts", {"host_path": "/h", "mount_path": "/m",
                          "host_path_type": "Dir"}, "host_path_type"),
    ("configmap_mounts", {"config_map_name": "Bad_Name", "mount_path": "/m"}, "resource name"),
    ("configmap_mounts", {"config_map_name": "cm", "mount_path": "m"}, "absolute mount_path"),
    ("configmap_mounts", {"config_map_name": "cm", "mount_path": "/m",
                          "sub_path": "/abs"}, "relative path"),
    ("configmap_mounts", {"config_map_name": "cm", "mount_path": "/m",
                          "items": [{"key": "k", "file": "p"}]}, "exactly"),
    ("configmap_mounts", {"config_map_name": "cm", "mount_path": "/m",
                          "items": [{"key": "", "path": "p"}]}, "non-empty string"),
    ("pvc_mounts", {"claim_name": "", "mount_path": "/m"}, "resource name"),
    ("pvc_mounts", {"claim_name": "p", "mount_path": "/m", "mode": "rw"}, "unknown keys"),
])
def test_mount_rejections(kind, item, match):
    fn = {"host_path_mounts": canonical_host_path_mounts,
          "configmap_mounts": canonical_configmap_mounts,
          "pvc_mounts": canonical_pvc_mounts}[kind]
    with pytest.raises(InvalidParams, match=match):
        fn([item], f"agent_{kind}")


def test_mount_non_list_rejected():
    with pytest.raises(InvalidParams, match="must be a list"):
        canonical_pvc_mounts({"claim_name": "p"}, "agent_pvc_mounts")


# -------------------------------------------------------------- 冲突检测

def test_find_mount_path_conflicts():
    hp = [{"host_path": "/h", "mount_path": "/etc/cfg",
           "read_only": False, "host_path_type": None}]
    cm = [{"config_map_name": "cm", "mount_path": "/etc/cfg",
           "sub_path": None, "items": None, "read_only": True}]
    assert find_mount_path_conflicts([("hp", hp), ("cm", cm)]) is not None
    cm2 = [dict(cm[0], mount_path="/etc/other")]
    assert find_mount_path_conflicts([("hp", hp), ("cm", cm2)]) is None
    # 撞主容器 NFS 挂载点
    assert find_mount_path_conflicts(
        [("hp", [dict(hp[0], mount_path="/data")])],
        extra_paths=["/data"]) is not None


def test_validate_agent_mounts_strict_and_none():
    out = validate_agent_mounts(
        None, None, None, nfs_mount_path="/data")
    assert out == (None, None, None)
    out = validate_agent_mounts(
        [{"host_path": "/h", "mount_path": "/m"}],
        [{"config_map_name": "cm", "mount_path": "/cfg"}],
        [{"claim_name": "pvc", "mount_path": "/vol"}],
        nfs_mount_path="/data")
    assert out[0][0]["host_path"] == "/h"
    assert out[1][0]["config_map_name"] == "cm"
    assert out[2][0]["claim_name"] == "pvc"
    # mount_path 撞 nfs → 400
    with pytest.raises(InvalidParams, match="duplicated"):
        validate_agent_mounts(
            [{"host_path": "/h", "mount_path": "/data"}], None, None,
            nfs_mount_path="/data")


# -------------------------------------------------------------- 归一 + 指纹

@pytest.mark.parametrize("kind,value", [
    ("host_path_mounts", None), ("host_path_mounts", []),
    ("configmap_mounts", "garbage"), ("pvc_mounts", [{}]),
    ("pvc_mounts", [{"claim_name": "p", "mount_path": "/v"}, "bad"]),
])
def test_normalize_mounts_tolerant(kind, value):
    out = normalize_mounts(value, kind)
    if value in (None, [], "garbage", [{}]):
        assert out is None
    else:  # 坏项丢弃,合法项保留为规范形
        assert out == [{"claim_name": "p", "mount_path": "/v", "read_only": False}]


def test_agent_mounts_fingerprint_stability():
    """承重:无挂载/空列表 → None → 指纹与旧字段集逐字节相等;顺序重排同指纹。"""
    legacy = Template(template_id="t", agent_image="i:1")
    empty = Template(template_id="t", agent_image="i:1",
                     agent_host_path_mounts=[], agent_configmap_mounts=[],
                     agent_pvc_mounts=[])
    assert empty == legacy and empty.deploy_ver() == legacy.deploy_ver()

    reordered = Template(
        template_id="t", agent_image="i:1",
        agent_pvc_mounts=[{"claim_name": "b", "mount_path": "/b"},
                          {"claim_name": "a", "mount_path": "/a"}],
        agent_configmap_mounts=[{"config_map_name": "cm", "mount_path": "/cfg"}])
    ordered = Template(
        template_id="t", agent_image="i:1",
        agent_pvc_mounts=[{"claim_name": "a", "mount_path": "/a"},
                          {"claim_name": "b", "mount_path": "/b"}],
        agent_configmap_mounts=[{"config_map_name": "cm", "mount_path": "/cfg",
                                 "read_only": True, "sub_path": None, "items": None}])
    assert reordered.deploy_ver() == ordered.deploy_ver()
    # 挂载变更 → A 类(指纹变)
    changed = Template(template_id="t", agent_image="i:1",
                       agent_configmap_mounts=[{"config_map_name": "cm2",
                                                "mount_path": "/cfg"}])
    assert changed.deploy_ver() != ordered.deploy_ver()
