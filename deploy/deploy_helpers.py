"""Small independent deployment IO helpers; never start polling or migrate a DB."""
import argparse
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib import request


class DeployError(Exception):
    pass


def readonly_db(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30)


def check_database(path):
    with closing(readonly_db(path)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise DeployError('SQLite integrity_check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise DeployError('SQLite foreign_key_check failed.')


def backup_database(source,destination,*,max_seconds=30):
    destination=Path(destination)
    source=Path(source).resolve()
    if destination.resolve()==source or destination.exists() and destination.samefile(source):
        raise DeployError('Backup destination aliases the source database.')
    # Exclusive reservation: never overwrite a previous backup or follow a link.
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    reserved=os.fstat(fd);os.close(fd)
    deadline=time.monotonic()+max_seconds
    def progress(status,remaining,total):
        if time.monotonic()>deadline:
            raise DeployError('SQLite snapshot exceeded its bounded backup window.')
    try:
        with closing(readonly_db(source)) as source_conn,closing(sqlite3.connect(destination)) as backup:
            source_conn.backup(backup,pages=256,progress=progress,sleep=0.05)
        if not destination.is_file():raise DeployError('Backup file is missing.')
        check_database(destination)
        return destination
    except BaseException:
        # Only remove the exact file reserved by this invocation.
        if destination.exists() and not destination.is_symlink():
            current=destination.stat()
            if (current.st_dev,current.st_ino)==(reserved.st_dev,reserved.st_ino):
                destination.unlink()
        raise


def enabled_worlds(db):
    with closing(readonly_db(db)) as conn:
        rows=conn.execute('SELECT id,chat_id,thread_id FROM mini_worlds WHERE enabled=1 ORDER BY id').fetchall()
    if not rows:raise DeployError('No enabled Mini worlds: notice was not sent.')
    worlds=[]
    for identifier,chat,thread in rows:
        if not chat or thread is None or thread<0:raise DeployError('Invalid Mini chat/topic configuration.')
        worlds.append(dict(id=identifier,chat_id=chat,thread_id=thread))
    return worlds


def telegram_send(token,world,text,*,opener=None):
    payload=dict(chat_id=world['chat_id'],text=text)
    if world['thread_id']:payload['message_thread_id']=world['thread_id']
    req=request.Request('https://api.telegram.org/bot'+token+'/sendMessage',
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'},method='POST')
    # Never expose an exception/URL/body: urllib errors can contain the bot token.
    try:
        with (opener or request.urlopen)(req,timeout=15) as response:
            body=json.loads(response.read())
        if not body.get('ok'):raise DeployError('Telegram rejected sendMessage.')
    except DeployError:raise
    except Exception as exc:
        raise DeployError('Telegram transport failed ('+type(exc).__name__+').') from None


def send_notices(db,token,text,*,sender=None):
    if not token:raise DeployError('BOT_TOKEN is missing.')
    if not text.strip():raise DeployError('Notice text is empty.')
    failures=[]
    for world in enabled_worlds(db):
        try:(sender or telegram_send)(token,world,text)
        except Exception:
            failures.append(world['id'])
    if failures:raise DeployError('Telegram notice failed for Mini worlds: '+', '.join(map(str,failures)))


def runtime_config(project):
    sys.path.insert(0,str(Path(project).resolve()))
    return importlib.import_module('app.config')


def notice_main(argv=None):
    parser=argparse.ArgumentParser(description='Send a deployment notice to all enabled Mini topics.')
    parser.add_argument('--project',required=True)
    parser.add_argument('--text',required=True)
    args=parser.parse_args(argv)
    try:
        config=runtime_config(args.project)
        send_notices(config.DB_PATH,config.TOKEN,args.text)
    except Exception as exc:
        # Only our own errors have safe text. Never print config/environment exceptions.
        print('ERROR: '+(str(exc) if isinstance(exc,DeployError) else 'Notice configuration/database unavailable.'),file=sys.stderr)
        return 1
    print('Telegram notice: OK')
    return 0


def sqlite_main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('backup','check','config'))
    parser.add_argument('--db',required=True)
    parser.add_argument('--destination')
    parser.add_argument('--project')
    args=parser.parse_args(argv)
    try:
        if args.action=='config':
            if not args.project or Path(runtime_config(args.project).DB_PATH).resolve()!=Path(args.db).resolve():
                raise DeployError('Runtime DB_PATH does not match the production DB.')
            print('Runtime DB_PATH: OK')
        elif args.action=='backup':
            if not args.destination:raise DeployError('Backup destination is required.')
            backup_database(args.db,args.destination)
            print(args.destination)
        else:
            check_database(args.db)
            print('SQLite integrity/FK: OK')
    except Exception as exc:
        print('ERROR: '+(str(exc) if isinstance(exc,DeployError) else 'SQLite backup/check failed.'),file=sys.stderr)
        return 1
    return 0


def redact_output(env_path,lines,write):
    from dotenv import dotenv_values
    values=dotenv_values(env_path,interpolate=False)
    secrets=[piece for key,value in values.items() if value and key!='BOT_TIMEZONE'
             for piece in value.splitlines() if len(piece)>=6]
    for line in lines:
        line=re.sub(r'\b\d{5,20}:[A-Za-z0-9_-]{20,}\b','[REDACTED]',line)
        for secret in secrets:line=line.replace(secret,'[REDACTED]')
        write(line)


if __name__=='__main__':
    # The shell uses this as a streaming console/log filter, never prints .env.
    parser=argparse.ArgumentParser()
    parser.add_argument('--redact-env',required=True)
    args=parser.parse_args()
    redact_output(args.redact_env,sys.stdin,lambda line: print(line,end='',flush=True))
