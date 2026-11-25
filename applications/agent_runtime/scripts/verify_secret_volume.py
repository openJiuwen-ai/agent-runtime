"""Exercise template Secret mounts on Kubernetes with a real AgentServer image.

Uses the reference Pod's image/envFrom, isolated emptyDir storage and short-lived
test certificates. Creates only uniquely named test Pods/Secrets and removes them
on exit. Does not change existing workloads, templates, databases or Redis keys.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import subprocess
import time
import uuid
from unittest.mock import patch

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from kubernetes_asyncio import client as sdk

from agent_runtime.link_mtls import LinkMTLSConfig, LinkMTLSMode
from agent_runtime.resource_manager.k8s import RealK8sPodClient
from agent_runtime.session_manager.container_spec import (
    build_canonical, canonical_volumes, parse_container_spec,
)
from agent_runtime.session_manager.models import Template


def certificates():
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Secret mount test CA")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(ca_key, hashes.SHA256()))
    return {
        "ca.crt": ca.public_bytes(serialization.Encoding.PEM),
        "tls.crt": cert.public_bytes(serialization.Encoding.PEM),
        "tls.key": key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()),
    }


TLS_SERVER = """
import http.server, ssl
server = http.server.HTTPServer(('0.0.0.0', 9443), http.server.BaseHTTPRequestHandler)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'secret-ca-ok')
server.RequestHandlerClass = Handler
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/etc/test-tls/server.crt', '/etc/test-tls/server.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
"""

CLIENT_CHECK = """
import os, pathlib, httpx
assert os.environ['SSL_CERT_FILE'] == '/etc/test-ca/ca-bundle.pem'
files = {p.name for p in pathlib.Path('/etc/test-ca').iterdir() if not p.name.startswith('.')}
assert files == {'ca-bundle.pem'}, files
try:
    httpx.get('https://localhost:9443/', trust_env=False, timeout=10)
except httpx.ConnectError as exc:
    assert 'CERTIFICATE_VERIFY_FAILED' in str(exc), str(exc)
else:
    raise AssertionError('Untrusted test CA unexpectedly accepted')
response = httpx.get('https://localhost:9443/', timeout=10)
assert response.status_code == 200 and response.text == 'secret-ca-ok'
print('PASS: key projection, untrusted-CA rejection, SSL_CERT_FILE verification')
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oc", default="oc")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--reference-pod", required=True)
    args = parser.parse_args()
    prefix = "secret-volume-check-" + uuid.uuid4().hex[:8]
    command = [args.oc, "--kubeconfig", args.kubeconfig, "-n", args.namespace,
               "--request-timeout=30s"]

    def run(*argv, body=None):
        result = subprocess.run(command + list(argv), input=body, text=True,
                                capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout

    reference = json.loads(run("get", "pod", args.reference_pod, "-o", "json"))
    image = reference["spec"]["containers"][0]["image"]
    resources = {"requests": {"cpu": "0", "memory": "128Mi"},
                 "limits": {"cpu": "500m", "memory": "1Gi"}}
    definitions = canonical_volumes([
        {"name": "ca", "secret": {"secretName": prefix + "-ca", "items": [
            {"key": "ca.crt", "path": "ca-bundle.pem"}]}},
        {"name": "tls", "secret": {"secretName": prefix + "-tls", "items": [
            {"key": "tls.crt", "path": "server.crt"},
            {"key": "tls.key", "path": "server.key"}]}},
        {"name": "workspace", "emptyDir": {}},
    ], "volumes")
    main_wire = {
        "container_id": "agent", "name": "agent", "image": image,
        "ports": [{"name": "sse", "containerPort": 8766}],
        "envFrom": reference["spec"]["containers"][0].get("envFrom", []),
        "env": [{"name": k, "value": v} for k, v in {
            "SSL_CERT_FILE": "/etc/test-ca/ca-bundle.pem", "NO_PROXY": "localhost,127.0.0.1",
            "AGENT_HTTP_ENABLED": "true", "AGENT_HTTP_HOST": "0.0.0.0",
            "AGENT_HTTP_PORT": "8766", "OTEL_ENABLED": "false"}.items()],
        "volumeMounts": [{"name": "ca", "mountPath": "/etc/test-ca", "readOnly": True},
                         {"name": "workspace", "mountPath": "/home/app/.jiuwenswarm"}],
        "resources": resources,
        "readinessProbe": {"httpGet": {"path": "/api/v1/health", "port": 8766}},
    }
    side_wire = {"container_id": "tls", "name": "tls", "image": image,
                 "command": ["python", "-c", TLS_SERVER], "resources": resources,
                 "ports": [{"containerPort": 9443}],
                 "volumeMounts": [{"name": "tls", "mountPath": "/etc/test-tls"}]}
    def canonical(wire, role):
        return build_canonical(parse_container_spec(wire, role, role=role),
                               definitions, role, role=role)
    template = Template(template_id=prefix, namespace=args.namespace,
                        main_container=canonical(main_wire, "main"),
                        sidecars=[canonical(side_wire, "sidecar")])
    renderer = RealK8sPodClient(link_mtls_config=LinkMTLSConfig(mode=LinkMTLSMode.OFF))
    # Use the SDK serializer to preserve native Kubernetes camelCase field names.
    with patch.object(renderer, "_client", sdk):
        native = renderer.build_pod_body(prefix, template.deploy_subset())
    async def serialize():
        async with sdk.ApiClient() as api:
            return api.sanitize_for_serialization(native)
    pod = asyncio.run(serialize())
    pod["metadata"]["labels"] = {"secret-volume-test": prefix}
    pod["spec"]["automountServiceAccountToken"] = False
    pod["spec"]["activeDeadlineSeconds"] = 600
    material = certificates()
    with open(certifi.where(), "rb") as bundle:
        material["ca.crt"] += bundle.read()
    created = []
    try:
        run("create", "--dry-run=server", "-f", "-", body=json.dumps(pod))
        for suffix, keys in [("ca", ["ca.crt"]), ("tls", ["tls.crt", "tls.key"])]:
            secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {
                "name": prefix + "-" + suffix, "labels": {"secret-volume-test": prefix}},
                "data": {k: base64.b64encode(material[k]).decode() for k in keys}}
            run("create", "-f", "-", body=json.dumps(secret))
            created.append(("secret", secret["metadata"]["name"]))
        run("create", "-f", "-", body=json.dumps(pod))
        created.append(("pod", prefix))
        print("Created isolated AgentServer test Pod:", prefix, flush=True)
        for attempt in range(16):
            status = json.loads(run("get", "pod", prefix, "-o", "json"))
            if any(c["type"] == "Ready" and c["status"] == "True"
                   for c in status.get("status", {}).get("conditions", [])):
                break
            print("Waiting for real AgentServer readiness:", attempt + 1, flush=True)
            time.sleep(10)
        else:
            raise RuntimeError("AgentServer test Pod did not become Ready")
        print(run("exec", prefix, "-c", "agent", "--", "python", "-c", CLIENT_CHECK), flush=True)
        print("PASS: real AgentServer readiness and HTTPS trust via mounted Secret", flush=True)
    finally:
        for kind, name in reversed(created):
            run("delete", kind, name, "--ignore-not-found", "--wait=false")
        print("Removed isolated test resources", flush=True)


if __name__ == "__main__":
    main()
