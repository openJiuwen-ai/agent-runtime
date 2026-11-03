"""Initialize requested credentials in the isolated local database only."""
import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / '.manager-dev' / 'identity.db'
os.environ['IDENTITY_DB_TYPE'] = 'sqlite'
os.environ['IDENTITY_SQLITE_PATH'] = str(DB)
os.environ['IDENTITY_ADMIN_PASSWORD'] = 'admin'
os.environ['IDENTITY_USER1_PASSWORD'] = 'user'

async def main():
    from identity_center.infrastructure.db import create_db_handler
    from identity_center.models.table_init import init_all_tables
    from identity_center.core.auth.service import _ensure_local_user, seed_defaults
    from identity_center.core.auth.password import hash_password
    handler = create_db_handler()
    await handler.init_database()
    await handler.connect()
    await init_all_tables(handler)
    await seed_defaults(handler)
    await _ensure_local_user(handler, user_id='user', display_name='Local User', is_admin=False,
                             username='user', password='user')
    for username in ('admin', 'user'):
        await handler.update('auth_identity', {'provider': 'local', 'external_subject': username},
                             {'credential': hash_password(username)})
    await handler.disconnect()
    print('Local credentials initialized: admin/admin, user/user')

if __name__ == '__main__':
    asyncio.run(main())
