"""Identity Center adapters for Service Framework federation contracts."""

from .login_service import IdentityFederationService
from .provider import DemoFederationProvider
from .store import IdentityCenterFederatedIdentityStore

__all__ = [
    "DemoFederationProvider",
    "IdentityCenterFederatedIdentityStore",
    "IdentityFederationService",
]
