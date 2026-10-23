"""Linux CI-only root probe: temporary SQLite + nobody, no server paths or app imports."""
from contextlib import closing
import json
import os
from pathlib import Path
import pwd
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.staging import controller as c


def schema(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('CREATE TABLE mini_worlds(chat_id,thread_id,enabled)')
        conn.execute('INSERT INTO mini_worlds VALUES(-1003883723333,2,1)')
        conn.execute('CREATE TABLE sample(value)')
        conn.execute("INSERT INTO sample VALUES('preserved')")
        conn.commit()


def main():
    if os.geteuid() != 0:
        raise RuntimeError('This isolated Linux runner probe requires sudo')
    account = pwd.getpwnam('nobody')
    with tempfile.TemporaryDirectory(prefix='staging-privilege-probe-') as tmp:
        root = Path(tmp); root.chmod(0o755)
        data = root/'data';data.mkdir(mode=0o700);os.chown(data,account.pw_uid,account.pw_gid)
        private = root/'root-only';private.mkdir(mode=0o700)
        source = data/'test.db'
        with patch('pwd.getpwnam', return_value=account):
            with c.as_test_user():
                schema(source)
                writer = sqlite3.connect(source)
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute("INSERT INTO sample VALUES('committed WAL')");writer.commit()
            try:
                result = c.backup(source, private/'before.db')
                assert result['integrity']=='ok'
                with closing(sqlite3.connect(private/'before.db')) as conn:
                    assert conn.execute('SELECT COUNT(*) FROM sample').fetchone()[0]==2
                assert (private/'before.db').stat().st_uid==0
                c.db_check(source,scope=True,as_bot=True)
            finally:
                writer.close()
            secret = private/'secret.db';schema(secret)
            source.unlink();source.symlink_to(secret)
            try:
                c.backup(source, private/'must-not-read-root.db')
            except (PermissionError,sqlite3.OperationalError):
                pass
            else:
                raise AssertionError('Application symlink exposed root-only SQLite')
            assert os.geteuid()==0, 'Effective UID was not restored after refusal'
        print(json.dumps({'root_reserved_backup':'PASS','wal_backup':'PASS',
                          'app_symlink_to_root_db':'REFUSED','privilege_restoration':'PASS'}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
