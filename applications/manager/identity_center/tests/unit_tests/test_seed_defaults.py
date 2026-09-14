"""Bootstrap passwords come from Identity Center configuration."""

import pytest
from identity_center.core.auth.password import verify_password
from identity_center.core.auth.service import seed_defaults
from identity_center.infrastructure.config import settings
from identity_center.models.table_init import init_all_tables
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler


@pytest.mark.asyncio
async def test_seed_passwords_are_configured_and_existing_users_are_not_reset(tmp_path, monkeypatch):
    handler = SQLiteHandler(str(tmp_path / "identity.db"))
    await handler.init_database()
    await handler.connect()
    try:
        await init_all_tables(handler)
        monkeypatch.setattr(settings, "seed_admin", True)
        monkeypatch.setattr(settings, "seed_user1", True)
        monkeypatch.setattr(settings, "admin_password", "admin-custom&\"password")
        monkeypatch.setattr(settings, "user1_password", "user1-custom: # password")

        await seed_defaults(handler)

        admin = await handler.get("identity_user", {"user_id": "admin"})
        user1 = await handler.get("identity_user", {"user_id": "user1"})
        admin_identity = (await handler.list_records(
            "auth_identity", {"user_id": "admin"}, limit=1, offset=0,
        ))[0]
        user1_identity = (await handler.list_records(
            "auth_identity", {"user_id": "user1"}, limit=1, offset=0,
        ))[0]
        assert admin.is_admin is True
        assert user1.is_admin is False
        assert verify_password("admin-custom&\"password", admin_identity.credential)
        assert verify_password("user1-custom: # password", user1_identity.credential)
        assert admin_identity.credential != "admin-custom&\"password"

        monkeypatch.setattr(settings, "admin_password", "changed")
        monkeypatch.setattr(settings, "user1_password", "changed")
        await seed_defaults(handler)
        identities = await handler.list_records("auth_identity", {}, limit=10, offset=0)
        assert len(identities) == 2
        credentials = {identity.user_id: identity.credential for identity in identities}
        assert verify_password("admin-custom&\"password", credentials["admin"])
        assert verify_password("user1-custom: # password", credentials["user1"])
        assert not verify_password("changed", credentials["admin"])
        assert not verify_password("changed", credentials["user1"])
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_missing_password_cannot_create_a_user(tmp_path, monkeypatch):
    handler = SQLiteHandler(str(tmp_path / "identity.db"))
    await handler.init_database()
    await handler.connect()
    try:
        await init_all_tables(handler)
        monkeypatch.setattr(settings, "seed_admin", True)
        monkeypatch.setattr(settings, "seed_user1", False)
        monkeypatch.setattr(settings, "admin_password", None)
        with pytest.raises(RuntimeError, match="IDENTITY_ADMIN_PASSWORD"):
            await seed_defaults(handler)
        assert await handler.get("identity_user", {"user_id": "admin"}) is None
    finally:
        await handler.disconnect()
