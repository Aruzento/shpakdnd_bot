"""Read-only downgrade gate. A flag is never proof for code which ignores it."""
import subprocess
from pathlib import Path

from app.dnd_shop.catalog import CatalogError

# Reviewed releases without an ordinary D&D trading interface. Other targets
# must be reviewed explicitly; absence of one filename is not a safety proof.
NO_SHOP_REFS = frozenset((
    '5e4d4495a189380542cc643c5c8d723b9351b36b',
    'd9856b2038a36490f93a4f5613ba7887698ac7e8',
))
CONTROL_FILES = (
    'app/dnd_shop/control.py', 'app/dnd_shop/service.py', 'app/dnd_shop/pricing.py',
    'app/db/dnd_shop.py', 'app/handlers/shop.py', 'app/dnd_shop/catalog.py',
    'app/dnd_shop/money.py', 'app/dnd_shop/builder.py',
    'app/dnd_shop/coins.py', 'app/dnd_shop/formatting.py',
    'app/dnd_shop/coin_migration.py', 'app/db/inventory.py',
)


def rollback_check(conn, target_ref, root):
    if not target_ref:
        raise CatalogError('rollback-check требует --target-ref: состояние /stopshop не доказывает безопасность старого /shop.')
    root = Path(root)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)
    try:
        target = git('rev-parse', '--verify', '--end-of-options', target_ref + '^{commit}').decode().strip()
        if conn.execute('SELECT 1 FROM dnd_shop_publications LIMIT 1').fetchone():
            raise CatalogError('Сначала завершите незавершённую публикацию.')
        known = conn.execute('''SELECT chat_id,thread_id FROM dnd_shop_skus
            UNION SELECT chat_id,thread_id FROM dnd_shop_settings
            UNION SELECT chat_id,thread_id FROM dnd_shop_credit''').fetchall()
        for scope in known:
            state = conn.execute('SELECT enabled FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?', tuple(scope)).fetchone()
            if not state or state[0]:
                raise CatalogError('Сначала /stopshop во всех существующих темах; обязательства сохраняются.')
        if target in NO_SHOP_REFS:
            capability = 'reviewed target without ordinary D&D shop'
        else:
            # Only exactly the current reviewed controls count as flag-aware.
            # Do not guess capabilities from version labels or a string in a file.
            try:
                matches = git('rev-parse', target + ':app') == git('rev-parse', 'HEAD:app') and all(
                    git('show', target + ':' + path).replace(b'\r\n', b'\n') ==
                    (root / path).read_bytes().replace(b'\r\n', b'\n') for path in CONTROL_FILES)
            except subprocess.CalledProcessError:
                matches = False
            if not matches:
                raise CatalogError('Целевая версия имеет старый/непроверенный /shop. /stopshop игнорируется старым кодом. Откат заблокирован; требуется отдельная техническая блокировка интерфейса вне приложения и её ревью.')
            capability = 'identical persistent stop/credit controls'
    except subprocess.CalledProcessError as error:
        raise CatalogError('Не удалось подтвердить точный коммит целевой версии.') from error
    liabilities = conn.execute("SELECT count(*) FROM dnd_shop_credit WHERE balance!='0'").fetchone()[0]
    return dict(target_sha=target, trading=capability, credit_owners=liabilities,
                preserve_database=True, preserve_all_shop_tables=True,
                deployment_gate='manual preflight; protected deployment bundle unchanged')
