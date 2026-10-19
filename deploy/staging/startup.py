"""Run as sandboxed TEST user only. Validate runtime paths/scope before application startup."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path('/opt/shpakdnd-staging/current')
DATA = Path('/var/lib/shpakdnd-staging/data')
CONFIG = Path('/etc/shpakdnd-staging')
sys.path.insert(0, str(ROOT))
import app.config as config
from app.topics import TOPIC_SETTINGS

if (config.ENV_PATH.resolve() != CONFIG/'.env' or config.BASE_DIR.resolve() != ROOT.resolve()
        or config.TOKEN != '123456:offline-staging-check' or config.TIMEZONE_NAME != 'Europe/Moscow'
        or os.environ.get('MINI_EDITORIAL_DIR') != str(CONFIG/'editorial')):
    raise RuntimeError('Runtime config escaped approved TEST environment')
if config.DB_PATH.resolve() != DATA / 'shpakdnd.db' or config.LEGACY_DB_PATH.resolve() != DATA / 'timers.db':
    raise RuntimeError('Runtime SQLite escaped TEST data directory')
scope = {(chat, topic) for chat, topics in TOPIC_SETTINGS.items() for topic in topics}
mini = {(chat, topic) for chat, topics in TOPIC_SETTINGS.items() for topic, cfg in topics.items() if cfg.get('mini')}
if scope != {(-1003883723333, 2), (-5320347021, 0)} or mini != {(-1003883723333, 2)}:
    raise RuntimeError('Runtime topics escaped TEST scope')
runpy.run_path(str(ROOT / 'check_bot.py'), run_name='__main__')
print('Frozen runtime path/scope and startup checks: PASS')
