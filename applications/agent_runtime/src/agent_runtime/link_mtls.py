# coding: utf-8
"""Runtime 内部 HTTP/SSE 链路的 mTLS 与实例绑定配置。

默认 ``off`` 保持现有 HTTP、Pod 探针和地址生成逻辑完全不变；``observe``
只校验证书材料并记录预检结果；只有 ``enforce`` 才启用 HTTPS、双向证书校验、
绑定头校验及 AgentServer Secret 挂载。
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from openjiuwen_runtime.foundation.security.link_mtls_config import (
    CA_FILE_ENV,
    CERT_FILE_ENV,
    KEY_FILE_ENV,
    MODE_ENV,
    LinkMTLSMode,
    MTLSDeploymentIdentity,
)
from openjiuwen_runtime.foundation.security.link_profile import (
    PROFILE_ENV,
    LinkProfile,
    LinkProfileError,
    load_service_identity,
)

logger = logging.getLogger("agent_runtime.link_mtls")

AGENTSERVER_SECRET_ENV = "AGENT_RUNTIME_LINK_MTLS_AGENTSERVER_SECRET"
AGENTSERVER_HEADLESS_SERVICE_ENV = "AGENT_RUNTIME_LINK_MTLS_HEADLESS_SERVICE"
CLUSTER_DOMAIN_ENV = "AGENT_RUNTIME_LINK_MTLS_CLUSTER_DOMAIN"
AGENTSERVER_MOUNT_DIR_ENV = "AGENT_RUNTIME_LINK_MTLS_MOUNT_DIR"

DEFAULT_HEADLESS_SERVICE = "jiuwenclaw-agentserver"
DEFAULT_CLUSTER_DOMAIN = "cluster.local"
DEFAULT_MOUNT_DIR = "/etc/jiuwenswarm/link-mtls"


LinkMTLSError = LinkProfileError


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@dataclass(frozen=True)
class LinkMTLSConfig:
    mode: LinkMTLSMode

    profile: LinkProfile | None = None
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None
    identity: MTLSDeploymentIdentity | None = None
    agentserver_secret: str = ""
    headless_service: str = DEFAULT_HEADLESS_SERVICE
    cluster_domain: str = DEFAULT_CLUSTER_DOMAIN
    mount_dir: str = DEFAULT_MOUNT_DIR

    @classmethod
    def from_env(cls) -> "LinkMTLSConfig":
        try:
            mode = LinkMTLSMode(_env(MODE_ENV) or "off")
        except ValueError as exc:
            raise LinkMTLSError(f"{MODE_ENV} must be off, observe or enforce") from exc

        shared = dict(
            agentserver_secret=_env(AGENTSERVER_SECRET_ENV),
            headless_service=_env(AGENTSERVER_HEADLESS_SERVICE_ENV)
            or DEFAULT_HEADLESS_SERVICE,
            cluster_domain=_env(CLUSTER_DOMAIN_ENV) or DEFAULT_CLUSTER_DOMAIN,
            mount_dir=_env(AGENTSERVER_MOUNT_DIR_ENV) or DEFAULT_MOUNT_DIR,
        )
        if mode is LinkMTLSMode.OFF:
            return cls(mode=mode, **shared)
        try:
            profile = load_service_identity("runtime")
        except (ValueError, OSError) as exc:
            if mode is LinkMTLSMode.ENFORCE:
                raise LinkMTLSError(str(exc)) from exc
            logger.warning("link mTLS observe preflight failed: %s", exc)
            return cls(mode=mode, **shared)
        if mode is LinkMTLSMode.OBSERVE:
            logger.info(
                "link mTLS observe material preflight passed; business transport unchanged"
            )
            return cls(mode=mode, **shared)
        return cls(
            mode=mode,
            profile=profile,
            ca_file=profile.ca_file,
            cert_file=profile.cert_file,
            key_file=profile.key_file,
            identity=MTLSDeploymentIdentity(
                profile.mtls_deployment_id,
                profile.mtls_binding_id,
                profile.mtls_binding_epoch,
            ),
            **shared,
        )

    def resolve_endpoint(self, url: str, *, role: str) -> str:
        if self.profile is not None:
            return self.profile.endpoint(url, role=role, enforced=self.enforced)
        self.require_secure_url(url, label=f"{role} URL")
        return url

    def client_kwargs(self, *, role: str) -> dict:
        if not self.enforced:
            return {}
        if self.profile is None:
            raise LinkMTLSError("enforce requires a pinned deployment profile")
        return self.profile.client_kwargs(role=role)

    def authorize_request(self, request) -> None:
        if self.enforced:
            if self.profile is None:
                raise LinkMTLSError("enforce requires a pinned deployment profile")
            self.profile.authorize(request.scope, request.headers)

    @property
    def enforced(self) -> bool:
        return self.mode is LinkMTLSMode.ENFORCE

    def client_ssl_context(self) -> ssl.SSLContext | None:
        if not self.ca_file or not self.cert_file or not self.key_file:
            return None
        context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=self.ca_file,
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(certfile=self.cert_file, keyfile=self.key_file)
        return context

    def uvicorn_ssl_kwargs(self) -> dict[str, object]:
        if not self.enforced:
            return {}
        if self.profile is None:
            raise LinkMTLSError("enforce requires a pinned deployment profile")
        return self.profile.server_kwargs()

    def binding_headers(self) -> dict[str, str]:
        if self.profile is not None:
            return self.profile.headers()
        return self.identity.headers() if self.identity is not None else {}

    def route_metadata(self) -> dict[str, str | int]:
        if not self.enforced or self.identity is None:
            return {}
        return {
            "mtls_deployment_id": self.identity.mtls_deployment_id,
            "mtls_binding_id": self.identity.mtls_binding_id,
            "mtls_binding_epoch": self.identity.mtls_binding_epoch,
        }

    def validate_incoming(self, headers: Mapping[str, str]) -> None:
        if self.enforced:
            raise LinkMTLSError(
                "header-only validation is unsafe; use authorize_request with TLS peer scope"
            )

    def require_secure_url(self, url: str, *, label: str) -> None:
        if self.enforced and urlsplit(url).scheme.lower() != "https":
            raise LinkMTLSError(
                f"{label} must use https:// when {MODE_ENV}=enforce: {url!r}"
            )

    def agentserver_url(
        self,
        *,
        pod_id: str,
        namespace: str,
        port: int,
        path: str,
        pod_ip: str,
    ) -> str:
        if not self.enforced:
            return f"http://{pod_ip}:{port}{path}"
        if self.profile is not None and "agentserver" in self.profile.current().get(
            "endpoints", {}
        ):
            return (
                self.profile.endpoint("link://agentserver", role="agentserver") + path
            )
        hostname = (
            f"{pod_id}.{self.headless_service}.{namespace}.svc.{self.cluster_domain}"
        )
        return f"https://{hostname}:{port}{path}"

    def agentserver_env(self) -> dict[str, str]:
        if not self.enforced or self.identity is None:
            return {}
        mount = self.mount_dir.rstrip("/")
        return {
            PROFILE_ENV: f"{mount}/profile.json",
            MODE_ENV: LinkMTLSMode.ENFORCE.value,
            CA_FILE_ENV: f"{mount}/ca.crt",
            CERT_FILE_ENV: f"{mount}/tls.crt",
            KEY_FILE_ENV: f"{mount}/tls.key",
        }

    def cert_fingerprint(self) -> str:
        if not self.cert_file:
            return ""
        pem = Path(self.cert_file).read_text(encoding="utf-8")
        der = ssl.PEM_cert_to_DER_cert(pem)
        return hashlib.sha256(der).hexdigest()
