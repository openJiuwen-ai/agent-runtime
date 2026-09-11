# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Serialize real Kubernetes SDK objects; do not contact a cluster."""

import copy
from unittest.mock import patch

import pytest
from agent_runtime.link_mtls import MTLSDeploymentIdentity, LinkMTLSConfig, LinkMTLSMode
from agent_runtime.resource_manager.k8s import RealK8sPodClient


@pytest.mark.parametrize("mode", ["off", "observe", "enforce"])
@pytest.mark.parametrize("security", [(0, None), (0, 0), (1000, 1000)])
def test_canonical_container_settings_survive_certificate_injection(mode, security):
    sdk = pytest.importorskip("kubernetes_asyncio.client")

    uid, group = security
    shared = [
        {"claim_name": "business-data", "mount_path": "/data", "read_only": False}
    ]
    main = {
        "name": "agent",
        "image": "agentserver:21s",
        "command": ["python"],
        "args": ["-m", "agent"],
        "ports": [{"name": "sse", "container_port": 8766}],
        "env": {"BUSINESS": "unchanged", "JIUWENSWARM_LINK_MTLS_MODE": "off"},
        "env_from": [{"secret_ref": {"name": "business-secret"}}],
        "security_context": {"run_as_user": uid, "run_as_group": uid},
        "pvc_mounts": shared,
    }
    box = {"name": "jiuwenbox", "image": "box:21s", "pvc_mounts": shared}
    spec = {
        "namespace": "test",
        "main_container": main,
        "sidecars": [box],
        "fs_group": group,
    }
    original = copy.deepcopy(spec)
    config = LinkMTLSConfig(
        mode=LinkMTLSMode(mode),
        identity=MTLSDeploymentIdentity("instance", "binding", 1),
        agentserver_secret="agentserver-tls",
    )
    renderer = RealK8sPodClient(link_mtls_config=config)
    with patch.object(renderer, "_client", sdk):
        pod = renderer.build_pod_body("pod-test", spec).to_dict()["spec"]
    assert spec == original
    agent, sandbox = pod["containers"][:2]
    assert agent["command"] == main["command"] and agent["args"] == main["args"]
    assert agent["env_from"][0]["secret_ref"]["name"] == "business-secret"
    assert agent["security_context"]["run_as_user"] == uid
    assert (pod["security_context"] or {}).get("fs_group") == group
    assert len([v for v in pod["volumes"] if v["persistent_volume_claim"]]) == 1
    assert len(sandbox["volume_mounts"]) == 1  # business PVC only
    env = {item["name"]: item["value"] for item in agent["env"]}
    assert env["BUSINESS"] == "unchanged"
    assert env["JIUWENSWARM_LINK_MTLS_MODE"] == (
        "enforce" if mode == "enforce" else "off"
    )
    if mode != "enforce":
        assert len(pod["containers"]) == 2 and not pod["init_containers"]
        assert pod["hostname"] is None
        return
    assert pod["hostname"] == "pod-test"
    assert agent["readiness_probe"]["tcp_socket"]["port"] == 8766
    private_copy = uid != 0 or group is not None
    assert len(pod["containers"]) == (3 if private_copy else 2)
    secret = next(v["secret"] for v in pod["volumes"] if v["secret"])
    assert secret["default_mode"] == (0o444 if private_copy else 0o400)
    if private_copy:
        helper = pod["containers"][2]
        assert helper["env_from"] is None and helper["env"] is None
        assert helper["security_context"]["allow_privilege_escalation"] is False
        assert env["JIUWENSWARM_LINK_MTLS_KEY_FILE"].endswith(
            "/private/identity/tls.key"
        )
