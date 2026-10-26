"""Run repository checks without access to checkout SQLite or real Telegram tokens."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOOK = """import os, sys
from pathlib import Path
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import PathFinder
class ConfigLoader(Loader):
 def __init__(self, wrapped): self.wrapped=wrapped
 def create_module(self,spec): return self.wrapped.create_module(spec)
 def exec_module(self,module):
  self.wrapped.exec_module(module)
  root=Path(os.environ['DND_CHECK_SANDBOX'])
  module.DB_PATH=root/'startup.db'
  module.LEGACY_DB_PATH=root/'legacy-unused.db'
class ConfigFinder(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname!='app.config': return None
  spec=PathFinder.find_spec(fullname,path)
  if spec and Path(spec.origin).resolve()==Path(os.environ['DND_CHECK_ROOT'])/'app'/'config.py':
   spec.loader=ConfigLoader(spec.loader)
   return spec
sys.meta_path.insert(0,ConfigFinder())
# Additional fail-closed deployment preflight boundary. The normal test harness
# remains unchanged unless these explicit offline deployment settings are set.
if os.environ.get('DND_CHECK_DENY_PROJECT'):
 forbidden=Path(os.environ['DND_CHECK_DENY_PROJECT']).resolve()
 import socket
 socketpair_code=socket._fallback_socketpair.__code__
 def offline_audit(event,args):
  if event in ('open','sqlite3.connect') and args and isinstance(args[0],(str,bytes,os.PathLike)):
   raw=os.fsdecode(args[0])
   if raw.startswith('file:'):
    from urllib.parse import urlsplit,unquote
    parsed=urlsplit(raw);raw=unquote(parsed.path)
    if parsed.netloc: raw='//'+parsed.netloc+raw
    if os.name=='nt' and len(raw)>2 and raw[0]=='/' and raw[2]==':': raw=raw[1:]
   path=Path(raw).resolve()
   if path==forbidden/'.env' or (path.parent==forbidden and path.name.startswith(('shpakdnd.db','timers.db'))):
    raise PermissionError('Offline preflight cannot open live config/SQLite')
  if event=='socket.connect' and os.environ.get('DND_CHECK_OFFLINE')=='1':
   # Windows asyncio's self-pipe uses stdlib socketpair's own temporary
   # listener. Permit only that exact code/object/destination, never ordinary
   # localhost connections (which may reach a live service).
   frame=sys._getframe(1)
   if os.name=='nt' and frame.f_code is socketpair_code:
    local=frame.f_locals
    listener=local.get('lsock')
    if (local.get('csock') is args[0] and listener is not None
        and tuple(args[1])==listener.getsockname()[:2]
        and args[1][0] in ('127.0.0.1','::1')):
     return
   raise PermissionError('Offline preflight cannot connect to a network')
 sys.addaudithook(offline_audit)
"""


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="checks")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sandbox = Path(tempfile.mkdtemp(prefix="dnd-v15-" + args.label + "-"))
    startup = sandbox / "python"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
    env = dict(
        os.environ,
        BOT_TOKEN="123456:offline-v15-checks",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        PYTHONUNBUFFERED="1",
        DND_CHECK_SANDBOX=str(sandbox),
        DND_CHECK_ROOT=str(root),
        STORIES_DIR=str(sandbox / "stories"),
    )
    env["PYTHONPATH"] = str(startup) + os.pathsep + str(root)
    command = args.command or ["-m", "unittest", "discover", "-s", "tests", "-q"]
    if command and command[0] == "--":
        command = command[1:]
    log = sandbox / "output.log"
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.run(
            [sys.executable, *command],
            cwd=root,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    report = {
        "exit_code": proc.returncode,
        "command": command,
        "sandbox": str(sandbox),
        "log": str(log),
    }
    (sandbox / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    print(log.read_text(encoding="utf-8")[-18000:])
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
