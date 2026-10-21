"""One-shot option B for the approved TEST scope; no public guard bypass."""
import ast
import hashlib
import json
import socket
import sqlite3
import subprocess
import time
from contextlib import closing, contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from app.db.dnd_shop import migrate
from app.dnd_shop.audit import audit
from app.dnd_shop.builder import REFERENCE_SHA256, encode_products, read_master, verify_snapshot
from app.dnd_shop.catalog import CatalogError, CatalogBusy, bounded_bytes, catalog_lock, parse_catalog
from app.dnd_shop.control import PLAN_KEYS, persist_state, publish_locked
from app.dnd_shop.diagnostics import analyze
from app.dnd_shop.money import exact_text
from app.dnd_shop.pricing import preflight
from app.dnd_shop.service import Shop

SCOPE = (-5320347021, 0)
HOST = 'VM-338333'
DB = Path('/opt/shpakdnd-bot/shpakdnd.db')
RUNTIME = Path('/var/lib/shpakdnd-v144-stage/shop.csv')
LOOT = Path('/var/lib/shpakdnd-v144-stage/loot.csv')
ID = 'test-b-source-copper-v2--5320347021-0'
ROOT = Path(__file__).resolve().parents[2]
MAX_AGE = 7200
# A duplicate may join a PROVEN durable same-plan operation, never extend the
# generic catalog lock timeout or retry monetary writes. A crashed owner still
# has a finite wait and leaves the existing prepared journal for explicit resume.
RECEIPT_WAIT = 5


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def revision():
    return subprocess.check_output(['git', '-c', 'safe.directory='+str(ROOT), 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def literal_topics(source):
    """Only literal assignments to TOPIC_SETTINGS; no executing captured Python."""
    values = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            raise CatalogError('Captured configuration requires separate review.')
        value = ast.literal_eval(node.value)
        target = node.targets[0]
        keys = []
        while isinstance(target, ast.Subscript):
            keys.insert(0, ast.literal_eval(target.slice))
            target = target.value
        if not isinstance(target, ast.Name) or target.id != 'TOPIC_SETTINGS':
            raise CatalogError('Unexpected captured configuration target.')
        if not keys:
            values = value
        else:
            parent = values
            for key in keys[:-1]:
                parent = parent[key]
            parent[keys[-1]] = value
    if not isinstance(values, dict) or values.get(SCOPE[0], {}).get(SCOPE[1], {}).get('mini'):
        raise CatalogError('Invalid TEST topics.')
    return values


def scoped_rows(conn):
    """Exact typed projections, including pending tokens; exclude derived caches."""
    tables = ('inventory', 'characters', 'character_profiles', 'dnd_items', 'dnd_currencies',
              'dnd_shop_settings', 'dnd_shop_skus', 'dnd_shop_owners', 'dnd_shop_sessions',
              'dnd_shop_trades', 'dnd_shop_credit', 'dnd_shop_credit_moves')
    result = {}
    for table in tables:
        result[table] = conn.execute('SELECT * FROM '+table+' WHERE chat_id=? AND thread_id=? ORDER BY rowid', SCOPE).fetchall()
    for table in ('dnd_shop_operations', 'dnd_shop_credit_redemptions'):
        result[table] = conn.execute('SELECT o.* FROM '+table+''' o JOIN dnd_shop_sessions s
            ON s.token=o.session_token WHERE s.chat_id=? AND s.thread_id=? ORDER BY o.rowid''', SCOPE).fetchall()
    result['dnd_shop_money'] = conn.execute('''SELECT m.* FROM dnd_shop_money m JOIN dnd_shop_trades t
        ON t.id=m.trade_id WHERE t.chat_id=? AND t.thread_id=? ORDER BY m.rowid''', SCOPE).fetchall()
    result['dnd_shop_credit_coins'] = conn.execute('''SELECT c.* FROM dnd_shop_credit_coins c
        JOIN dnd_shop_credit_redemptions r ON r.token=c.redemption_token
        JOIN dnd_shop_sessions s ON s.token=r.session_token
        WHERE s.chat_id=? AND s.thread_id=? ORDER BY c.rowid''', SCOPE).fetchall()
    return {table: [[repr(v) for v in row] for row in rows] for table, rows in result.items()}


def fingerprint(conn, *, economic=False):
    rows = scoped_rows(conn)
    if economic:
        for table in ('dnd_currencies', 'dnd_shop_settings', 'dnd_shop_sessions'):
            rows.pop(table)
    else:
        rows['_schema'] = [list(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    return digest(canonical(rows).encode())


def capture(backup, metadata, runtime, loot):
    backup, runtime = Path(backup), Path(runtime)
    if backup.stat().st_size>64*1024*1024 or Path(metadata).stat().st_size>524288:
        raise CatalogError('Capture exceeds the reviewed size budget.')
    metadata_bytes = Path(metadata).read_bytes()
    meta = json.loads(metadata_bytes)
    meta['_metadata_sha256'] = digest(metadata_bytes)
    data = bounded_bytes(runtime)
    if (meta['hostname'] != HOST or tuple(meta['scope']) != SCOPE
            or meta['git_sha'] not in ('07170497d1bd375b5bd9f265112eddc7e024d08c', '6ef5d8bbac29f2b938b9c21448e1466abe676dc1')
            or meta['db_path'] != str(DB.as_posix())
            or digest(backup.read_bytes()) != meta['sha256']
            or digest(data) != meta['runtime_sha256']
            or digest(meta['topics_source'].encode()) != meta['topics_sha256']):
        raise CatalogError('Backup / metadata / runtime provenance mismatch.')
    master = read_master(loot)
    if master.digest != REFERENCE_SHA256 or meta['master_sha256'] != master.digest or len(master.rows) != 4450:
        raise CatalogError('Original 4450-row loot catalog required.')
    topics = literal_topics(meta['topics_source'])
    return meta, data, master, topics


def fresh(meta):
    age = (datetime.now(timezone.utc)-datetime.strptime(meta['captured_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)).total_seconds()
    if not 0 <= age <= MAX_AGE:
        raise CatalogError('Backup is not fresh (maximum 2 hours); capture and review a new plan.')


def change_nominals(conn, changes):
    for change in changes:
        count = conn.execute('''UPDATE dnd_currencies SET base_value=? WHERE item_id=?
            AND chat_id=? AND thread_id=? AND base_value=? AND currency_quantity=?''',
            (change['after_base_value'], change['item_id'], *SCOPE, change['before_base_value'],
             change['currency_quantity'])).rowcount
        if count != 1:
            raise CatalogError('Currency changed since backup; no automatic repair.')


def confirmed_master(shop, master):
    # Parsing may happen before the lock; every live use still verifies the
    # current bytes, including read failures. Never fall back to a stale master.
    if digest(bounded_bytes(shop.loot_path)) != master.digest:
        raise CatalogError('Master changed during migration; keep closed and review.')
    return master


def sync_existing(shop, conn, candidate, *, catalog=None):
    # A migration must not register a missing item or touch another user's data.
    if catalog is None:
        catalog = shop.catalog_from_bytes(candidate)
    if catalog.digest != digest(candidate):
        raise CatalogError('Parsed candidate differs from approved publication.')
    allowed = {'dnd_shop_catalog_entries', 'dnd_shop_catalog_versions'}
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
    conn.set_authorizer(lambda action, table, *args: sqlite3.SQLITE_DENY
                        if action in writes and table not in allowed else sqlite3.SQLITE_OK)
    try:
        shop.sync(conn, catalog, force=True)
    finally:
        conn.set_authorizer(None)


def candidate_for(shop, conn, data, master, profile):
    products = tuple(parse_catalog(data))
    factors = {(r['chat_id'], r['thread_id']): Fraction(r['factor'])
               for r in conn.execute('SELECT * FROM dnd_shop_settings')}
    factors[SCOPE] = Fraction('1/10000')
    candidate = encode_products([
        replace(p, price=master.by_name[p.name.casefold()].price * Fraction('1/10000'))
        if (p.chat_id, p.thread_id) == SCOPE else p for p in products])
    verify_snapshot(parse_catalog(candidate), master, factors)
    before = fingerprint(conn, economic=True)
    conn.execute('SAVEPOINT validate_test_b')
    persist_state(conn, SCOPE, profile, False)
    sync_existing(shop, conn, candidate)
    if fingerprint(conn, economic=True) != before:
        raise CatalogError('Candidate would change registry, ownership, stock counters or history.')
    conn.execute('ROLLBACK TO validate_test_b')
    conn.execute('RELEASE validate_test_b')
    return candidate


def prepare(backup, metadata, runtime, loot, *, check_age=True):
    meta, data, master, topics = capture(backup, metadata, runtime, loot)
    if check_age:
        fresh(meta)
    shop = Shop(backup, runtime, topics, loot_path=loot)
    with closing(sqlite3.connect(Path(backup).resolve().as_uri()+'?mode=ro', uri=True)) as source, closing(sqlite3.connect(':memory:')) as conn:
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        source.backup(conn)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.row_factory = sqlite3.Row
        if ([tuple(row) for row in conn.execute('PRAGMA integrity_check')] != [('ok',)]
                or conn.execute('PRAGMA foreign_key_check').fetchall()):
            raise CatalogError('Backup integrity / foreign keys failed; no repair is permitted.')
        original = fingerprint(conn)
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='dnd_shop_settings'").fetchone()
        if not ddl or not any(marker in ddl[0] for marker in
                              ('CHECK(settings_version=1)', 'CHECK(settings_version IN (1,2))')):
            raise CatalogError('Unexpected shop settings schema; separate review required.')
        state = conn.execute('SELECT * FROM dnd_shop_settings WHERE chat_id=? AND thread_id=?', SCOPE).fetchone()
        if not state or state['enabled'] or state['pricing_mode']!='m' or state['factor']!='1' or state['settings_version']!=1:
            raise CatalogError('Expected a stopped historical -m v1 store.')
        if (conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_test_migrations'").fetchone()
                and conn.execute('SELECT 1 FROM dnd_shop_test_migrations LIMIT 1').fetchone()):
            raise CatalogError('Use the original pre-migration backup for the one-shot plan.')
        if conn.execute('SELECT COUNT(*) FROM dnd_currencies WHERE chat_id=? AND thread_id=?', SCOPE).fetchone()[0]!=15:
            raise CatalogError('Exactly 15 approved monetary positions are required.')
        if conn.execute('SELECT 1 FROM dnd_shop_publications LIMIT 1').fetchone():
            raise CatalogError('Unfinished publication requires separate recovery.')
        trades = conn.execute('SELECT id,kind,price,value FROM dnd_shop_trades WHERE chat_id=? AND thread_id=? ORDER BY id', SCOPE).fetchall()
        if len(trades)!=11 or tuple(trades[-1])!=(11,'sell','35000','1050'):
            raise CatalogError('Expected exactly 11 approved trades including the unchanged payout 1050.')
        report = analyze(conn, SCOPE, master=master)
        if not report['wallet_totals_complete'] or len(report['monetary'])!=15 or report['proposed_factor']!='1/10000':
            raise CatalogError('Incomplete wallet analysis or unsupported base.')
        codes = dict(conn.execute('SELECT code,id FROM dnd_items WHERE chat_id=? AND thread_id=?', SCOPE))
        configured = {name.casefold(): codes[code] for name,code in topics[SCOPE[0]][SCOPE[1]]['shop_currency_units']['master'].items()}
        if configured != json.loads(state['currency_units']):
            raise CatalogError('Captured and stored monetary mappings differ.')
        # Prove the saved old signature and all bundles before changing anything.
        old_profile = preflight(shop, conn, SCOPE, 'b', master)
        if any(old_profile[k]!=state[k] for k in ('base_item_id','currency_signature','currency_units')):
            raise CatalogError('Old currency settings differ from the captured registry.')
        changes=[]
        for row in report['monetary']:
            current=conn.execute('SELECT * FROM dnd_currencies WHERE item_id=?',(row['item_id'],)).fetchone()
            changes.append(dict(item_id=row['item_id'], code=row['code'], source_name=row['source_name'],
                before_base_value=current['base_value'], currency_quantity=current['currency_quantity'],
                before_nominal=row['current'], after_nominal=row['proposed'],
                after_base_value=exact_text(Fraction(row['proposed'])*current['currency_quantity'])))
        migrate(conn)
        immutable=fingerprint(conn, economic=True)
        change_nominals(conn, changes)
        profile=preflight(shop,conn,SCOPE,'m',master)
        candidate=candidate_for(shop,conn,data,master,profile)
        persist_state(conn,SCOPE,profile,False)
        conn.execute('UPDATE dnd_shop_sessions SET closed=1,expires=0 WHERE chat_id=? AND thread_id=?',SCOPE)
        sync_existing(shop,conn,candidate)
        if fingerprint(conn,economic=True)!=immutable:
            raise CatalogError('Dry-run changed immutable economic values.')
        dry_audit=audit(conn)
        if (digest(Path(backup).read_bytes())!=meta['sha256']
                or digest(bounded_bytes(runtime))!=meta['runtime_sha256']
                or digest(Path(metadata).read_bytes())!=meta['_metadata_sha256']):
            raise CatalogError('Evidence changed during dry-run.')
        return dict(format=1, id=ID, option='B', hostname=HOST, scope=list(SCOPE), target_sha=revision(),
                    backup_sha256=meta['sha256'], metadata_sha256=meta['_metadata_sha256'],
                    topics_sha256=meta['topics_sha256'], captured_utc=meta['captured_utc'],
                    source_fingerprint=original, economic_fingerprint=immutable,
                    runtime_sha256=meta['runtime_sha256'], candidate_sha256=digest(candidate),
                    master_sha256=master.digest, currencies=changes, profile=profile,
                    wallets=report['wallets'], unchanged_trade_11=True, keep_inventory_quantities=True,
                    leave_enabled=False, dry_run_audit=dry_audit), candidate


def require_target(shop, meta, plan):
    if socket.gethostname()!=HOST:
        raise CatalogError('Apply is restricted to VM-338333.')
    if (shop.db_path.absolute()!=DB or shop.csv_path!=RUNTIME or shop.loot_path.absolute()!=LOOT
            or str(shop.db_path.resolve())!=meta.get('db_resolved',str(DB))):
        raise CatalogError('Only the explicitly approved live TEST paths are allowed.')
    if revision()!=plan['target_sha']:
        raise CatalogError('The reviewed target code SHA differs from the plan.')
    import app.topics
    if any(digest(path.read_bytes())!=plan['topics_sha256'] for path in
           (Path(app.topics.__file__), Path('/opt/shpakdnd-v144-stage/app/topics.py'))):
        raise CatalogError('Live TEST topics differ from the captured settings.')
    try:
        subprocess.run(['git','-c','safe.directory='+str(ROOT),'diff','--exit-code','HEAD','--',
                        'app',':(exclude)app/topics.py','tools/migrate_dnd_shop_test_b.py'],
                       cwd=ROOT,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise CatalogError('Unreviewed code changes: apply is blocked.') from error


def publication_matches(row, candidate, plan):
    return bool(row and bytes(row['candidate'])==candidate and row['digest']==plan['candidate_sha256']
                and json.loads(row['plan'])==dict(plan['profile'],test_migration_id=ID))


def wait_for_receipt(shop, candidate, plan, approved_sha):
    """Read-only join after lock timeout, only for the identical durable intent."""
    deadline=time.monotonic()+RECEIPT_WAIT
    with closing(sqlite3.connect(shop.db_path.resolve().as_uri()+'?mode=ro',
                                 uri=True,timeout=0,isolation_level=None)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        def read(sql,args=()):
            # A writer's brief EXCLUSIVE commit can block read-only SELECTs.
            # Proof and receipt reads share ONE budget, without a hidden timeout.
            while True:
                try:
                    return conn.execute(sql,args).fetchone()
                except sqlite3.OperationalError as error:
                    code=getattr(error,'sqlite_errorcode',None)
                    if code is None or code & 0xff not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
                        raise
                    remaining=deadline-time.monotonic()
                    if remaining<=0:
                        return None
                    time.sleep(min(0.05,remaining))
        if not read("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_test_migrations'"):
            return None
        row=read("""SELECT m.*,p.candidate AS pending_candidate,
            p.plan AS pending_plan,p.digest AS pending_digest
            FROM dnd_shop_test_migrations m LEFT JOIN dnd_shop_publications p
            ON p.source_path=? WHERE m.id=?""",(str(shop.csv_path),ID))
        if (not row or row['plan_sha256']!=approved_sha or json.loads(row['plan'])!=plan
                or (row['chat_id'],row['thread_id'])!=SCOPE):
            return None
        if row['phase']=='complete':
            return json.loads(row['result'])
        pending = dict(candidate=row['pending_candidate'],plan=row['pending_plan'],
                       digest=row['pending_digest']) if row['pending_candidate'] is not None else None
        if row['phase']!='prepared' or not publication_matches(pending,candidate,plan):
            return None  # no evidence that this lock holder is our operation
        stored_plan=row['plan']
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                return None
            time.sleep(min(0.05,remaining))
            row=read('SELECT * FROM dnd_shop_test_migrations WHERE id=?',(ID,))
            if (not row or row['plan_sha256']!=approved_sha or row['plan']!=stored_plan
                    or (row['chat_id'],row['thread_id'])!=SCOPE):
                return None
            if row['phase']=='complete':
                return json.loads(row['result'])
            if row['phase']!='prepared':
                return None


@contextmanager
def migration_lock(shop, candidate, plan, approved_sha):
    # Catch ONLY failure to acquire EX. Errors from an acquired transaction must
    # propagate unchanged and must never be masked by a saved receipt.
    stack=ExitStack()
    try:
        stack.enter_context(catalog_lock(shop.csv_path,exclusive=True))
    except CatalogBusy:
        stack.close()
        receipt=wait_for_receipt(shop,candidate,plan,approved_sha)
        if receipt is None:
            raise
        yield receipt
    else:
        with stack:
            yield None


def apply(shop, backup, metadata, runtime_snapshot, plan, approved_sha):
    if digest(canonical(plan).encode())!=approved_sha:
        raise CatalogError('Explicit approved plan SHA-256 is required.')
    # Recompute the entire intended transformation from immutable evidence.
    checked, candidate = prepare(backup,metadata,runtime_snapshot,shop.loot_path,check_age=False)
    if checked!=plan:
        raise CatalogError('Plan differs from recomputed backup/settings transformation.')
    meta=json.loads(Path(metadata).read_text(encoding='utf-8'))
    require_target(shop,meta,plan)  # no CLI / environment escape hatch
    # Immutable, operation-local parsing is CPU-heavy for the full catalog.
    # Do it once before taking EX; SQL/master validation stays inside the lock.
    catalog = shop.catalog_from_bytes(candidate)
    master = read_master(shop.loot_path)
    if catalog.digest != plan['candidate_sha256'] or master.digest != plan['master_sha256']:
        raise CatalogError('Approved candidate / master differs.')
    with migration_lock(shop,candidate,plan,approved_sha) as receipt:
        if receipt is not None:
            require_target(shop,meta,plan)
            return receipt
        with shop.transaction(catalog=False) as (conn,_):
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='dnd_shop_test_migrations'").fetchone()
            record=conn.execute('SELECT * FROM dnd_shop_test_migrations WHERE id=?',(ID,)).fetchone() if exists else None
            if record:
                if record['plan_sha256']!=approved_sha or json.loads(record['plan'])!=plan:
                    raise CatalogError('A different one-shot plan already exists.')
                if record['phase']=='complete':
                    return json.loads(record['result'])
                if fingerprint(conn)!=record['prepared_fingerprint']:
                    raise CatalogError('State changed after preparation; keep closed and review.')
                pending=conn.execute('SELECT * FROM dnd_shop_publications WHERE source_path=?',(str(shop.csv_path),)).fetchone()
                if not publication_matches(pending,candidate,plan):
                    raise CatalogError('Migration publication journal differs.')
            else:
                if exists and conn.execute('SELECT 1 FROM dnd_shop_test_migrations LIMIT 1').fetchone():
                    raise CatalogError('A different one-shot migration already exists.')
                fresh(meta)
                if fingerprint(conn)!=plan['source_fingerprint']:
                    raise CatalogError('New operations or changed state since backup; capture a new plan.')
                if digest(bounded_bytes(shop.csv_path))!=plan['runtime_sha256']:
                    raise CatalogError('Live runtime changed since backup.')
                if ([tuple(row) for row in conn.execute('PRAGMA integrity_check')] != [('ok',)]
                        or conn.execute('PRAGMA foreign_key_check').fetchall()):
                    raise CatalogError('Live integrity / foreign keys failed; keep closed and review.')
                migrate(conn)
                if fingerprint(conn,economic=True)!=plan['economic_fingerprint']:
                    raise CatalogError('Historical economy differs.')
                change_nominals(conn,plan['currencies'])
                profile=preflight(shop,conn,SCOPE,'m',confirmed_master(shop,master))
                if any(profile[k]!=plan['profile'][k] for k in PLAN_KEYS):
                    raise CatalogError('Live currency / topic configuration differs.')
                persist_state(conn,SCOPE,profile,False)
                conn.execute('UPDATE dnd_shop_sessions SET closed=1,expires=0 WHERE chat_id=? AND thread_id=?',SCOPE)
                # Validate using exactly the normal sync; no persistent changes to
                # registries/counters/history are permitted.
                conn.execute('SAVEPOINT candidate')
                sync_existing(shop,conn,candidate,catalog=catalog)
                if fingerprint(conn,economic=True)!=plan['economic_fingerprint']:
                    raise CatalogError('Publication would modify historical economy.')
                conn.execute('ROLLBACK TO candidate')
                conn.execute('RELEASE candidate')
                publication=dict(plan['profile'],test_migration_id=ID)
                conn.execute('INSERT INTO dnd_shop_publications(source_path,chat_id,thread_id,plan,candidate,digest) VALUES(?,?,?,?,?,?)',
                             (str(shop.csv_path),*SCOPE,canonical(publication),candidate,plan['candidate_sha256']))
                conn.execute('''INSERT INTO dnd_shop_test_migrations
                    (id,chat_id,thread_id,plan_sha256,plan,prepared_fingerprint,phase)
                    VALUES(?,?,?,?,?,?,'prepared')''',
                    (ID,*SCOPE,approved_sha,canonical(plan),fingerprint(conn)))
                audit(conn)
        # Commit durable intent and a disabled shop before touching the file.
        current=digest(bounded_bytes(shop.csv_path))
        if current not in (plan['runtime_sha256'],plan['candidate_sha256']):
            raise CatalogError('Runtime differs; migration stays closed.')
        if current!=plan['candidate_sha256']:
            publish_locked(shop,candidate)
        with shop.transaction(catalog=False) as (conn,_):
            require_target(shop,meta,plan)
            record=conn.execute('SELECT * FROM dnd_shop_test_migrations WHERE id=?',(ID,)).fetchone()
            pending=conn.execute('SELECT * FROM dnd_shop_publications WHERE source_path=?',(str(shop.csv_path),)).fetchone()
            if (not record or record['phase']!='prepared' or fingerprint(conn)!=record['prepared_fingerprint']
                    or not publication_matches(pending,candidate,plan)):
                raise CatalogError('State changed before finalization; migration stays closed.')
            profile=preflight(shop,conn,SCOPE,'m',confirmed_master(shop,master))
            if any(profile[k]!=plan['profile'][k] for k in PLAN_KEYS):
                raise CatalogError('Currency profile changed during publication.')
            if digest(bounded_bytes(shop.csv_path))!=plan['candidate_sha256']:
                raise CatalogError('Published runtime differs.')
            sync_existing(shop,conn,candidate,catalog=catalog)
            if fingerprint(conn,economic=True)!=plan['economic_fingerprint']:
                raise CatalogError('Immutable economic values changed.')
            conn.execute('DELETE FROM dnd_shop_publications WHERE source_path=?',(str(shop.csv_path),))
            result=dict(id=ID,plan_sha256=approved_sha,option='B',enabled=False,
                        factor='1/10000',trades_preserved=11,quantities_unchanged=True,
                        candidate_sha256=plan['candidate_sha256'])
            conn.execute("""UPDATE dnd_shop_test_migrations SET phase='complete',result=?,
                completed_at=CURRENT_TIMESTAMP WHERE id=?""",(canonical(result),ID))
            result['audit']=audit(conn)
            conn.execute('UPDATE dnd_shop_test_migrations SET result=? WHERE id=?',(canonical(result),ID))
            return result
