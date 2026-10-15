"""Isolated VM topic injection only; never changes tracked game code/rules.
Copy this reviewed helper to a root-owned runtime directory outside checkout.
sitecustomize.py must contain: from staging_topics import install; install()
Set SHPAKDND_STAGING_ONLY=1, SHPAKDND_STAGING_TOPICS=<root-owned JSON>,
PYTHONPATH=<runtime dir> explicitly in the standalone staging bot unit.
JSON: {"chat_id": <negative staging group>, "thread_id": <positive topic>}.
"""
import importlib.abc
import importlib.machinery
import json
import os
TRUSTED_UID=0
from pathlib import Path


def configuration(path):
    path=Path(path)
    for part in (path,*path.parents):
        info=part.lstat()
        sticky_tmp=part!=path and part.is_dir() and info.st_mode & 0o1000 and info.st_uid in {0,TRUSTED_UID}
        owner_bad=info.st_uid!=TRUSTED_UID if part==path else info.st_uid not in {0,TRUSTED_UID}
        if part.is_symlink() or (os.name=='posix' and (owner_bad or info.st_mode & 0o022) and not sticky_tmp):
            raise RuntimeError('Untrusted staging topic configuration')
    raw=json.loads(path.read_text(encoding='utf-8'))
    if set(raw)!={'chat_id','thread_id'} or type(raw['chat_id']) is not int or raw['chat_id']>=0 or type(raw['thread_id']) is not int or raw['thread_id']<=0:
        raise RuntimeError('Explicit independent group/topic required')
    return raw


def install(config_path=None):
    if os.environ.get('SHPAKDND_STAGING_ONLY')!='1':raise RuntimeError('Staging injection requires explicit isolated VM unit')
    config=configuration(config_path or os.environ['SHPAKDND_STAGING_TOPICS'])
    class Loader(importlib.abc.Loader):
        def __init__(self,wrapped):self.wrapped=wrapped
        def create_module(self,spec):return self.wrapped.create_module(spec)
        def exec_module(self,module):
            self.wrapped.exec_module(module)
            if config['chat_id'] in module.TOPIC_SETTINGS:raise RuntimeError('Staging chat aliases existing configured production chat')
            module.TOPIC_SETTINGS={config['chat_id']:{config['thread_id']:{'mini':True,'mini_name':'Isolated release staging','characters':{}}}}
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path,target=None):
            if fullname!='app.topics':return None
            spec=importlib.machinery.PathFinder.find_spec(fullname,path,target)
            spec.loader=Loader(spec.loader);return spec
    import sys
    sys.meta_path.insert(0,Finder())
