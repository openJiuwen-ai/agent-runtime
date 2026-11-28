"""Secret 卷升级：旧 Pod hash、SM/RM 一致性与 DB JSON 往返。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_runtime.resource_manager.orchestrator import _deploy_ver
from agent_runtime.session_manager.models import Template
from agent_runtime.session_manager.container_spec import (
    build_canonical, canonical_volumes, container_row_from_spec,
    container_spec_from_row, parse_container_spec,
    volumes_from_column, volumes_to_column,
)
from agent_runtime.spec_fields import DEPLOY_VER_FIELDS
from agent_runtime.util import fingerprint


@pytest.mark.parametrize("role", ["main", "sidecar"])
def test_secret_empty_items_matches_omitted_and_null(role):
    """等价的 wire 配置必须得到相同容器规范形和 SM/RM 部署指纹。"""
    containers = []
    versions = []
    for extra in ({}, {"items": None}, {"items": []}):
        volumes = canonical_volumes([{
            "name": "ca", "secret": {"secretName": "shared-ca", **extra},
        }], "volumes")
        spec = parse_container_spec({
            "container_id": "c", "name": "probe", "image": "agent:1",
            "volumeMounts": [{"name": "ca", "mountPath": "/etc/ca"}],
        }, "container", role=role)
        container = build_canonical(spec, volumes, "container", role=role)
        template = Template(
            template_id="t",
            main_container=container if role == "main" else {"image": "agent:1"},
            sidecars=[container] if role == "sidecar" else None,
        )
        containers.append(container)
        versions.append(template.deploy_ver())
        assert template.deploy_ver() == _deploy_ver(template.deploy_subset())
    assert containers[0] == containers[1] == containers[2]
    assert containers[0]["secret_mounts"][0]["items"] is None
    assert versions[0] == versions[1] == versions[2]


@pytest.mark.parametrize("sidecars", [None, [{"name": "box", "image": "box:1"}]])
def test_no_secret_preserves_pre_upgrade_pod_hash(sidecars):
    template = Template(template_id="t", main_container={"image": "agent:1"},
                        sidecars=sidecars)
    current = template.deploy_subset()
    before = deepcopy(current)
    legacy = deepcopy(current)
    for container in [legacy["main_container"], *(legacy["sidecars"] or [])]:
        container.pop("secret_mounts")
    persisted_hash = fingerprint({k: legacy[k] for k in DEPLOY_VER_FIELDS})
    assert template.deploy_ver() == persisted_hash
    assert _deploy_ver(legacy) == _deploy_ver(current) == persisted_hash
    assert current == before


@pytest.mark.parametrize("target", ["main", "sidecar"])
def test_secret_reference_changes_version_and_order_does_not(target):
    mount = {"secret_name": "shared-ca", "mount_path": "/etc/ca",
             "items": [{"key": "root", "path": "root.pem"},
                       {"key": "issuer", "path": "issuer.pem"}]}

    def template(value):
        main = {"image": "agent:1"}
        side = {"name": "box", "image": "box:1"}
        (main if target == "main" else side)["secret_mounts"] = value
        return Template(template_id="t", main_container=main, sidecars=[side])

    original = template([mount])
    assert original.deploy_ver() == _deploy_ver(original.deploy_subset())
    assert original.deploy_ver() != template([]).deploy_ver()
    reordered = dict(mount, items=list(reversed(mount["items"])))
    assert original.deploy_ver() == template([reordered]).deploy_ver()
    for change in ({"secret_name": "other-ca"}, {"mount_path": "/etc/other"},
                   {"items": [{"key": "new-root", "path": "root.pem"}]},
                   {"sub_path": "root.pem"}):
        assert original.deploy_ver() != template([dict(mount, **change)]).deploy_ver()


def test_secret_survives_database_column_roundtrip():
    volumes = canonical_volumes([{"name": "ca", "secret": {
        "secretName": "shared-ca", "items": [{"key": "ca.crt", "path": "ca.pem"}]}
    }], "volumes")
    spec = parse_container_spec({"container_id": "c", "image": "agent:1",
        "volumeMounts": [{"name": "ca", "mountPath": "/etc/ca"}],
        "env": [{"name": "SSL_CERT_FILE", "value": "/etc/ca/ca.pem"}]},
        "container", role="main")
    expected = build_canonical(spec, volumes, "container", role="main")
    row = SimpleNamespace(**container_row_from_spec(spec))
    restored = build_canonical(container_spec_from_row(row),
        volumes_from_column(volumes_to_column(volumes)), "container", role="main")
    assert restored == expected
    assert restored["secret_mounts"][0]["secret_name"] == "shared-ca"
