"""Manager 产品角色与权限。"""

from .service import (
    PLATFORM_ADMIN_ROLE_ID,
    AuthzService,
    seed_authz_defaults,
)
from .identity_reference_count import count_org_references, count_user_references

__all__ = (
    "PLATFORM_ADMIN_ROLE_ID",
    "AuthzService",
    "count_org_references",
    "count_user_references",
    "seed_authz_defaults",
)
