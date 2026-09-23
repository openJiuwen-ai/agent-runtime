"""Manager 产品角色与权限。"""

from .service import (
    PLATFORM_ADMIN_ROLE_ID,
    AuthzService,
    seed_authz_defaults,
)

__all__ = ("PLATFORM_ADMIN_ROLE_ID", "AuthzService", "seed_authz_defaults")
