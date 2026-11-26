"""Prepare the namespace's user Web certificate before deploying Nginx."""

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import logging
import os
from pathlib import Path
import posixpath
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


LEAF = "jiuwenswarm-web-tls"
CA = "jiuwenswarm-web-dev-ca"
PROBE_TRUST = "jiuwenswarm-web-probe-ca"
OWNER = "jiuwenswarm.io/web-tls"
RENEW_SECONDS = 30 * 24 * 60 * 60


class Secrets:
    """Use kubeconfig on Kubernetes or the deploy tool's authenticated OCP API."""

    def __init__(self, namespace):
        self.namespace = namespace
        self.server = os.environ.get("WEB_TLS_CLUSTER_SERVER", "")

    def request(self, method, name, resource=None, *, kind="secret", namespace=None):
        namespace = namespace or self.namespace
        body = json.dumps(resource).encode() if resource else None
        if self.server:
            url = urlsplit(self.server)
            if url.scheme != "https":
                raise ValueError("OpenShift API requires HTTPS")
            context = ssl.create_default_context()
            if os.environ.get("WEB_TLS_CLUSTER_INSECURE") == "true":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(url.hostname, url.port, context=context, timeout=30)
            try:
                headers = {"Authorization": "Bearer " + os.environ["WEB_TLS_CLUSTER_TOKEN"],
                           "Content-Type": "application/json"}
                path = posixpath.join(url.path or "/", "api/v1/namespaces", namespace, f"{kind}s")
                if name:
                    path = posixpath.join(path, name)
                conn.request(method, path, body, headers)
                response = conn.getresponse()
                data = response.read()
                if response.status == 404 and method == "GET":
                    return None
                if response.status == 409:
                    raise FileExistsError("concurrent TLS update")
                if response.status not in (200, 201):
                    raise ValueError(f"Web TLS {kind} API returned HTTP {response.status}")
                return json.loads(data)
            finally:
                conn.close()
        args = ["kubectl", "-n", namespace]
        if method == "GET":
            args += ["get", kind, name, "--ignore-not-found", "-o", "json"]
        else:
            args += ["create" if method == "POST" else "replace", "-f", "-", "-o", "json"]
        result = subprocess.run(args, input=body, capture_output=True)
        if result.returncode:
            if b"AlreadyExists" in result.stderr or b"Conflict" in result.stderr:
                raise FileExistsError("concurrent TLS update")
            raise ValueError(f"kubectl {method} of Web TLS {kind} failed (exit {result.returncode})")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def save(self, name, kind, fields, previous=None):
        metadata = {"name": name, "namespace": self.namespace, "annotations": {OWNER: "deploy"}}
        if previous:
            metadata = dict(previous["metadata"])
        resource = {"apiVersion": "v1", "kind": "Secret", "metadata": metadata, "type": kind,
                    "data": {key: base64.b64encode(value).decode() for key, value in fields.items()}}
        return self.request("PUT" if previous else "POST", name if previous else "", resource)


def openssl(*args, check=True):
    directories = ["/usr/bin", "/bin", "/usr/local/bin"]
    if sys.platform == "darwin":
        directories[:0] = ["/opt/homebrew/opt/openssl@3/bin", "/usr/local/opt/openssl@3/bin"]
    executable = shutil.which("openssl", path=os.pathsep.join(directories))
    if executable is None:
        raise FileNotFoundError("OpenSSL executable not found in system installation directories")
    return subprocess.run([executable, *args], check=check, capture_output=True)


def names(value):
    entries = set()
    for item in value.split(","):
        host = item.strip().lower()
        if not host:
            continue
        try:
            entries.add(("IP", str(ipaddress.ip_address(host))))
            continue
        except ValueError:
            pass
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.split(".")):
            raise ValueError("WEB_TLS_HOSTS requires comma-separated DNS names or IP addresses")
        entries.add(("DNS", host))
    return entries


def export_fields(resource, directory, keys):
    for key in keys:
        (directory / key).write_bytes(base64.b64decode(resource["data"][key], validate=True))


def check_leaf(directory, hosts):
    cert, key = directory / "tls.crt", directory / "tls.key"
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
    san = openssl("x509", "-in", cert, "-noout", "-ext", "subjectAltName").stdout
    if b"Subject Alternative Name" not in san:
        raise ValueError("Web TLS certificate is missing subjectAltName")
    for kind, host in hosts:
        flag = "-verify_ip" if kind == "IP" else "-verify_hostname"
        openssl("verify", "-partial_chain", "-trusted", cert, "-purpose", "sslserver", flag, host, cert)
    openssl("verify", "-partial_chain", "-trusted", cert, "-purpose", "sslserver", cert)
    entries = set()
    for tag, value in re.findall(r"(DNS|IP Address):([^,\s]+)", san.decode()):
        if tag == "DNS":
            entries.add(("DNS", value.lower()))
        else:
            entries.add(("IP", str(ipaddress.ip_address(value))))
    return entries


def publish_probe_ca(store, manager_namespace, key, pem):
    """Publish one Service's public trust chain without overwriting peer entries."""
    current = store.request("GET", PROBE_TRUST, kind="configmap", namespace=manager_namespace)
    data = dict((current or {}).get("data", {}))
    if pem:
        # A customer-provided ca.crt can contain multiple certificates. Never publish private PEM data.
        blocks = re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
        if not blocks or b"".join(pem.split()) != b"".join(b"".join(block.split()) for block in blocks):
            raise ValueError("ca.crt must contain only public PEM certificates")
        ssl.create_default_context(cadata=pem.decode("ascii"))
        data[key] = pem.decode("ascii")
    else:
        data.pop(key, None)
    if data == (current or {}).get("data", {}):
        return
    resource = {"apiVersion": "v1", "kind": "ConfigMap", "data": data,
                "metadata": dict(current["metadata"]) if current else
                {"namespace": manager_namespace, "name": PROBE_TRUST}}
    store.request("PUT" if current else "POST", PROBE_TRUST if current else "", resource,
                  kind="configmap", namespace=manager_namespace)


def prepare(store, hosts, directory, ca_out, *, service="jiuwenswarm-web", manager_namespace=None):
    manager_namespace = manager_namespace or store.namespace
    fq_service = f"{service}.{store.namespace}"
    service_names = names(f"{service},{fq_service},{fq_service}.svc,{fq_service}.svc.cluster.local")
    probe_ca = None
    leaf = store.request("GET", LEAF)
    managed = leaf and leaf["metadata"].get("annotations", {}).get(OWNER) == "deploy"
    if leaf and not managed:
        if leaf.get("type") != "kubernetes.io/tls":
            raise ValueError(f"{LEAF} must be a kubernetes.io/tls Secret")
        export_fields(leaf, directory, ("tls.crt", "tls.key"))
        check_leaf(directory, hosts)
        if leaf.get("data", {}).get("ca.crt"):
            export_fields(leaf, directory, ("ca.crt",))
            # tls.crt may also carry intermediate certificates.
            openssl("verify", "-CAfile", directory / "ca.crt", "-untrusted", directory / "tls.crt",
                    "-purpose", "sslserver", directory / "tls.crt")
            probe_ca = (directory / "ca.crt").read_bytes()
    else:
        if not hosts:
            raise ValueError("Set WEB_TLS_HOSTS to the user Web address; use localhost for local forwarding only")
        hosts = hosts | service_names | names("localhost,127.0.0.1,::1")
        ca = store.request("GET", CA)
        ca_cert, ca_key = directory / "ca.crt", directory / "ca.key"
        if not ca:
            if leaf:
                raise ValueError(f"Restore {CA} before managing the existing certificate")
            openssl("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "1825",
                    "-subj", f"/CN=Jiuwen User Web Development CA {store.namespace}",
                    "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", ca_key, "-out", ca_cert)
            ca = store.save(CA, "Opaque", {"ca.crt": ca_cert.read_bytes(), "ca.key": ca_key.read_bytes()})
        if ca["metadata"].get("annotations", {}).get(OWNER) != "deploy":
            raise ValueError(f"Unexpected owner for {CA}")
        export_fields(ca, directory, ("ca.crt", "ca.key"))
        cert, key = directory / "tls.crt", directory / "tls.key"
        renew = True
        if leaf:
            export_fields(leaf, directory, ("tls.crt", "tls.key"))
            try:
                existing_hosts = check_leaf(directory, hosts)
                openssl("verify", "-CAfile", ca_cert, cert)
                expires_soon = openssl("x509", "-in", cert, "-checkend", str(RENEW_SECONDS), "-noout", check=False)
                renew = existing_hosts != hosts or expires_soon.returncode != 0
            except (ValueError, ssl.SSLError, subprocess.CalledProcessError):
                pass
        if renew:
            openssl("x509", "-in", ca_cert, "-checkend", str(90 * 86400), "-noout")
            extensions = directory / "server.ext"
            alt_names = ",".join(kind + ":" + host for kind, host in sorted(hosts))
            extensions.write_text(
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n"
                f"subjectAltName={alt_names}\n"
            )
            csr = directory / "server.csr"
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Jiuwen User Web",
                    "-keyout", key, "-out", csr)
            openssl("x509", "-req", "-in", csr, "-CA", ca_cert, "-CAkey", ca_key,
                    "-set_serial", "0x" + os.urandom(16).hex(), "-days", "90", "-sha256",
                    "-extfile", extensions, "-out", cert)
            check_leaf(directory, hosts)
            openssl("verify", "-CAfile", ca_cert, cert)
            store.save(LEAF, "kubernetes.io/tls", {"tls.crt": cert.read_bytes(), "tls.key": key.read_bytes()}, leaf)
        probe_ca = ca_cert.read_bytes()
    publish_probe_ca(store, manager_namespace, fq_service + ".crt", probe_ca)
    if probe_ca:
        with tempfile.NamedTemporaryFile(dir=ca_out.parent, delete=False) as output:
            output.write(probe_ca)
        os.replace(output.name, ca_out)
        fingerprint = openssl("x509", "-in", ca_out, "-noout", "-sha256", "-fingerprint").stdout.decode().strip()
        logging.getLogger(__name__).info("User Web public CA: %s (%s)", ca_out, fingerprint)
    else:
        ca_out.unlink(missing_ok=True)
    return hashlib.sha256((directory / "tls.crt").read_bytes()).hexdigest()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("namespace")
    parser.add_argument("hosts")
    parser.add_argument("ca_output", type=Path)
    parser.add_argument("--service", default="jiuwenswarm-web")
    args = parser.parse_args()
    store = Secrets(args.namespace)
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory() as temp:
                digest = prepare(
                    store, names(args.hosts), Path(temp), args.ca_output, service=args.service,
                    manager_namespace=os.environ.get("WEB_TLS_MANAGER_NAMESPACE"),
                )
                sys.stdout.write(f"{digest}\n")
            return
        except FileExistsError:
            if attempt == 2:
                raise


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        sys.exit(f"User Web TLS: {exc}")
