# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved
"""Exercise the certificate lifecycle using OpenSSL and an in-memory Secret API."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


DEPLOY = Path(__file__).resolve().parents[6] / "deploy"
SCRIPT = DEPLOY / "openshift/web_tls.py"
spec = importlib.util.spec_from_file_location("web_tls", SCRIPT)
tls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tls)


class TestSecrets(tls.Secrets):
    __test__ = False

    def __init__(self):
        super().__init__("web-test")
        self.objects = {}
        self.maps = {}

    def request(self, method, name, resource=None, *, kind="secret", namespace=None):
        if kind == "configmap":
            if method == "GET":
                return self.maps.get(namespace)
            self.maps[namespace] = resource
            return resource
        if method == "GET":
            return self.objects.get(name)
        self.objects[resource["metadata"]["name"]] = resource
        return resource


def test_standalone_deploy_tools_ship_same_certificate_helper():
    assert (DEPLOY / "jiuwenswarm-ee/web_tls.py").read_bytes() == SCRIPT.read_bytes()


def test_public_names_and_custom_certificate_lifecycle(tmp_path):
    store = TestSecrets()
    ca = tmp_path / "root.pem"
    hosts = tls.names("web.example.test,192.0.2.12")
    with pytest.raises(ValueError, match="WEB_TLS_HOSTS"):
        tls.prepare(store, set(), tmp_path, ca)
    digest = tls.prepare(store, hosts.copy(), tmp_path, ca)
    root = ca.read_bytes()
    cert_key = "jiuwenswarm-web.web-test.crt"
    assert store.maps["web-test"]["data"] == {cert_key: root.decode()}
    store.maps["manager"] = {"metadata": {"namespace": "manager", "name": tls.PROBE_TRUST},
                             "data": {"other.space.crt": "another anchor"}}
    assert tls.prepare(store, hosts, tmp_path, ca, manager_namespace="manager") == digest
    assert store.maps["manager"]["data"] == {"other.space.crt": "another anchor", cert_key: root.decode()}
    for dns in ["jiuwenswarm-web.web-test", "jiuwenswarm-web.web-test.svc.cluster.local"]:
        tls.openssl("verify", "-CAfile", ca, "-verify_hostname", dns, tmp_path / "tls.crt")
    for kind, value in tls.names("localhost,127.0.0.1,::1,192.0.2.12,web.example.test"):
        flag = "-verify_ip" if kind == "IP" else "-verify_hostname"
        tls.openssl("verify", "-CAfile", ca, flag, value, tmp_path / "tls.crt")
    assert tls.prepare(store, hosts.copy(), tmp_path, ca) == digest
    changed = tls.prepare(store, tls.names("web.example.test"), tmp_path, ca)
    assert changed != digest
    assert root == ca.read_bytes()
    leaf = store.objects[tls.LEAF]
    del leaf["metadata"]["annotations"][tls.OWNER]
    del store.objects[tls.CA]
    assert tls.prepare(store, tls.names("web.example.test"), tmp_path, ca) == changed
    with pytest.raises(subprocess.CalledProcessError):
        tls.prepare(store, tls.names("other.example.test"), tmp_path, ca)
    assert store.objects == {tls.LEAF: leaf}
    assert cert_key not in store.maps["web-test"]["data"]
    assert not ca.exists()
    leaf["data"]["ca.crt"] = base64.b64encode(root).decode()
    assert tls.prepare(store, hosts=tls.names("web.example.test"), directory=tmp_path, ca_out=ca) == changed
    assert store.maps["web-test"]["data"] == {cert_key: root.decode()}
    assert store.objects[tls.LEAF] is leaf


@pytest.mark.skipif(shutil.which("yq") is None, reason="deployment tool requires yq")
def test_ocp_web_reconciliation_emits_one_patch_per_resource(tmp_path):
    # The rendered input contains multiple YAML documents. The API accepts one JSON object per PATCH.
    manifest = (DEPLOY / "openshift/templates/web.template.yaml").read_text()
    manifest = manifest.replace("<<WEB_REPLICAS>>", "2").replace("<<WEB_IMAGE>>", "web:latest")
    (tmp_path / "web.yaml").write_text(manifest)
    manager = (DEPLOY / "openshift/templates/manager-server.template.yaml").read_text()
    manager = manager.replace("<<MANAGER_SERVER_IMAGE>>", "manager:candidate").replace("<<NAMESPACE>>", "web-test")
    (tmp_path / "manager-server.yaml").write_text(manager)
    fixture = Path(__file__).resolve().parent / "fixtures" / "reconcile_web_tls.sh"
    # Prefer the installed Bash (including Homebrew on macOS) and keep yq discoverable.
    tool_path = os.pathsep.join(("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", os.environ["PATH"]))
    subprocess.run([fixture, DEPLOY / "openshift", tmp_path], env={**os.environ, "PATH": tool_path}, check=True)
    pod = json.loads((tmp_path / "deployments-jiuwenswarm-web.json").read_text())["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"] == "web:latest"
    assert pod["containers"][0]["readinessProbe"]["httpGet"]["scheme"] == "HTTPS"
    assert pod["volumes"][0]["secret"]["secretName"] == tls.LEAF
    assert json.loads((tmp_path / "services-jiuwenswarm-web.json").read_text())["spec"]["ports"][0]["name"] == "https"
    route = json.loads((tmp_path / "routes-jiuwenswarm-web.json").read_text())
    assert route["spec"]["tls"]["termination"] == "passthrough"
    deployment = json.loads((tmp_path / "deployments-jiuwenswarm-manager-server.json").read_text())
    manager_pod = deployment["spec"]["template"]["spec"]
    assert manager_pod["volumes"][0]["configMap"]["name"] == tls.PROBE_TRUST
    container = manager_pod["containers"][0]
    assert container["image"] == "manager:candidate"
    assert container["volumeMounts"][0]["readOnly"] is True
    assert container["env"] == [
        {"name": "MANAGER_WEB_USER_WEB_TARGET", "value": "https://jiuwenswarm-web.web-test.svc.cluster.local:5173"}
    ]
