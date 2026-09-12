"""Optional multi-instance Manager client for authenticated internal links.

The installed certificate identifies the Manager service.  Target instance
identity, binding ID, epoch and peer pin come from ``instance_link_binding``;
they are deliberately not inferred from the Manager certificate profile's
deployment bootstrap ID.
"""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.security.link_mtls_config import (
    MODE_ENV,
    MTLS_BINDING_EPOCH_HEADER,
    MTLS_BINDING_ID_HEADER,
)
from openjiuwen_runtime.foundation.security.link_mtls_config import (
    LinkMTLSMode as ManagerLinkMTLSMode,
)
from openjiuwen_runtime.foundation.security.link_profile import (
    LinkProfile,
    LinkProfileError,
    load_service_identity,
)

from manager_server.models.link_binding_models import INSTANCE_LINK_BINDING_TABLE_DEF

logger = logging.getLogger(__name__)

ManagerLinkMTLSError = LinkProfileError


@dataclass(frozen=True)
class ManagerLinkTarget:
    endpoint: str
    headers: dict[str, str]
    client_kwargs: dict[str, Any]


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@dataclass(frozen=True)
class ManagerLinkMTLSConfig:
    mode: ManagerLinkMTLSMode
    profile: LinkProfile | None = None
    ca_file: str | None = None
    cert_file: str | None = None
    key_file: str | None = None

    @classmethod
    def from_env(cls) -> ManagerLinkMTLSConfig:
        try:
            mode = ManagerLinkMTLSMode(_env(MODE_ENV) or "off")
        except ValueError as exc:
            raise ManagerLinkMTLSError("invalid link mTLS mode") from exc
        if mode is ManagerLinkMTLSMode.OFF:
            return cls(mode=mode)
        try:
            profile = load_service_identity("manager")
        except (ValueError, OSError) as exc:
            if mode is ManagerLinkMTLSMode.ENFORCE:
                raise ManagerLinkMTLSError(str(exc)) from exc
            logger.warning("manager observe preflight failed: %s", exc)
            return cls(mode=mode)
        if mode is ManagerLinkMTLSMode.OBSERVE:
            return cls(mode=mode)
        return cls(
            mode=mode,
            profile=profile,
            ca_file=profile.ca_file,
            cert_file=profile.cert_file,
            key_file=profile.key_file,
        )

    def resolve_endpoint(self, url: str, *, role: str) -> str:
        if self.profile is not None:
            return self.profile.endpoint(url, role=role)
        self.require_secure_url(url, label=role)
        return url

    def client_kwargs(self, *, role: str) -> dict:
        if not self.enforced:
            return {}
        if self.profile is None:
            raise ManagerLinkMTLSError("enforce requires a pinned manager profile")
        return self.profile.client_kwargs(role=role)

    def health_client_kwargs(self) -> dict:
        """mTLS preflight before a Manager instance/binding row exists.

        Health checks authenticate the shared trust chain and hostname and
        present the Manager certificate, but cannot pin a target leaf until
        the instance-specific binding has been registered.  Configuration
        writes always use :meth:`target` and its exact database fingerprint.
        """
        if not self.enforced:
            return {}
        if self.profile is None:
            raise ManagerLinkMTLSError("enforce requires a pinned manager profile")
        self.profile.current()
        return {"verify": self.profile.ssl_context()}

    @property
    def enforced(self) -> bool:
        return self.mode is ManagerLinkMTLSMode.ENFORCE

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
        context.load_cert_chain(self.cert_file, self.key_file)
        return context

    def require_secure_url(self, url: str, *, label: str) -> None:
        if self.enforced and urlsplit(url).scheme.lower() != "https":
            raise ManagerLinkMTLSError(
                f"{label} must use https:// when {MODE_ENV}=enforce: {url!r}"
            )

    async def binding_headers(
        self, handler: DBHandler | None, jiuwenclaw_id: str
    ) -> dict[str, str]:
        if not self.enforced:
            return {}
        jid = str(jiuwenclaw_id or "").strip()
        if handler is None:
            raise ManagerLinkMTLSError("binding database is required in enforce mode")
        row = await self._binding_row(handler, jid)
        return self._headers(row)

    async def target(
        self,
        handler: DBHandler | None,
        jiuwenclaw_id: str,
        *,
        role: str,
        endpoint: str,
    ) -> ManagerLinkTarget:
        """Resolve one instance without coupling the Manager cert to its ID."""
        if role not in {"gateway", "runtime"}:
            raise ManagerLinkMTLSError("Manager target role must be gateway or runtime")
        resolved = self.resolve_endpoint(endpoint, role=role)
        if not self.enforced:
            return ManagerLinkTarget(resolved, {}, {})
        if self.profile is None:
            raise ManagerLinkMTLSError("enforce requires a pinned manager profile")
        if handler is None:
            raise ManagerLinkMTLSError("binding database is required in enforce mode")
        jid = str(jiuwenclaw_id or "").strip()
        row = await self._binding_row(handler, jid)
        bound_endpoint = str(getattr(row, f"mtls_{role}_endpoint", "") or "").strip()
        if _endpoint_authority(resolved) != _endpoint_authority(bound_endpoint):
            raise ManagerLinkMTLSError(
                f"{role} endpoint does not match the active mTLS link binding"
            )
        fingerprint_field = f"{role}_cert_fingerprint"
        expected_fingerprint = str(getattr(row, fingerprint_field, "") or "").strip()
        if not expected_fingerprint:
            raise ManagerLinkMTLSError(f"active link binding has no {role} certificate fingerprint")
        ca_file = self._trust_bundle_file(row)
        return ManagerLinkTarget(
            resolved,
            self._headers(row),
            self.profile.client_kwargs(
                role=role,
                expected_fingerprints={expected_fingerprint},
                ca_file=ca_file,
            ),
        )

    async def _binding_row(self, handler: DBHandler, jid: str) -> Any:
        if not jid:
            raise ManagerLinkMTLSError("jiuwenclaw_id is required")
        row: Any = await handler.get(
            INSTANCE_LINK_BINDING_TABLE_DEF.table_name,
            {"jiuwenclaw_id": jid},
        )
        if row is None and self.profile is not None:
            from manager_server.core.instance.link_binding_service import (
                InstanceLinkBindingService,
                LinkBindingConflict,
            )

            try:
                await InstanceLinkBindingService(handler).adopt_deployment_profile(
                    jid, self.profile
                )
            except (LinkBindingConflict, LookupError) as exc:
                # Two Manager workers can observe the missing row together.
                # Accept the winner's committed row; preserve real endpoint or
                # uniqueness conflicts when no row was created for this ID.
                concurrent = await handler.get(
                    INSTANCE_LINK_BINDING_TABLE_DEF.table_name,
                    {"jiuwenclaw_id": jid},
                )
                if concurrent is None:
                    raise ManagerLinkMTLSError(str(exc)) from exc
            row = await handler.get(
                INSTANCE_LINK_BINDING_TABLE_DEF.table_name,
                {"jiuwenclaw_id": jid},
            )
        if row is None or str(getattr(row, "status", "")) != "bound":
            raise ManagerLinkMTLSError(f"no active link binding for jiuwenclaw_id={jid!r}")
        self._validate_manager_identity(row)
        return row

    def _validate_manager_identity(self, row: Any) -> None:
        if self.profile is None:
            raise ManagerLinkMTLSError("enforce requires a pinned manager profile")
        data = row.data if isinstance(getattr(row, "data", None), dict) else {}
        expected = str(data.get("manager_cert_fingerprint", "") or "").strip()
        if not expected:
            raise ManagerLinkMTLSError("active link binding has no Manager certificate fingerprint")
        from openjiuwen_runtime.foundation.security.link_profile import cert_fingerprint

        if cert_fingerprint(self.profile.cert_file) != expected:
            raise ManagerLinkMTLSError(
                "installed Manager certificate is not authorized for this instance binding"
            )

    @staticmethod
    def _headers(row: Any) -> dict[str, str]:
        epoch = int(getattr(row, "mtls_binding_epoch", 0) or 0)
        mtls_binding_id = str(getattr(row, "mtls_binding_id", "") or "").strip()
        if not mtls_binding_id or epoch <= 0:
            raise ManagerLinkMTLSError("invalid active mTLS link binding")
        return {
            MTLS_BINDING_ID_HEADER: mtls_binding_id,
            MTLS_BINDING_EPOCH_HEADER: str(epoch),
        }

    def _trust_bundle_file(self, row: Any) -> str:
        if self.profile is None:
            raise ManagerLinkMTLSError("enforce requires a pinned manager profile")
        reference = str(getattr(row, "trust_bundle_ref", "") or "").strip()
        if reference == "profile://ca":
            return self.profile.ca_file
        if reference.startswith("file://"):
            path = Path(reference.removeprefix("file://"))
            if not path.is_absolute() or not path.is_file():
                raise ManagerLinkMTLSError("binding trust bundle file is unavailable")
            return str(path)
        raise ManagerLinkMTLSError(
            "unsupported trust bundle reference; mount it and use file:///absolute/path"
        )


def httpx_verify(config: ManagerLinkMTLSConfig) -> ssl.SSLContext | bool:
    """Return an httpx ``verify`` value without changing the default off path."""
    return config.client_ssl_context() or True


def _endpoint_authority(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    try:
        valid = (
            parsed.scheme in {"", "http", "https"}
            and parsed.hostname
            and parsed.port
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return ""
    return parsed.netloc.lower() if valid else ""
