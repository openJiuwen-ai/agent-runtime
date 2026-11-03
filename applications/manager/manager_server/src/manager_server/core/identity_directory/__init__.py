"""身份目录只读访问（identity 库）。

供 skill_upload 组织授权反查用户所属组织、管理面授权选择器拉目录。
不通过 foundation ORM 注册，直接用 SQLAlchemy 原生查询，避免在 manager-server
里重复 identity_center 的表定义。
"""

from .service import IdentityDirectoryService

__all__ = ["IdentityDirectoryService"]
