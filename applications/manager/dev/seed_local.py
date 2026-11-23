"""Reset only the two requested accounts in the isolated local SQLite database."""
from pathlib import Path
import sqlite3
from identity_center.core.auth.password import hash_password

root = Path(__file__).resolve().parents[3]
database = root / '.manager-dev' / 'identity.db'
if not database.exists():
    raise SystemExit('Start local.sh once before seeding local accounts.')
with sqlite3.connect(database) as db:
    # Keep internal IDs and memberships stable; user1 becomes the login name user.
    for subject, login, password in [('admin', 'admin', 'admin'), ('user1', 'user', 'user')]:
        result = db.execute(
            'UPDATE auth_identity SET external_subject=?, credential=? WHERE user_id=? AND provider=?',
            (login, hash_password(password), subject, 'local'),
        )
        if result.rowcount != 1:
            raise RuntimeError(f'Expected one local credential for {subject}; no changes committed')
print('Local accounts ready: admin/admin and user/user')
