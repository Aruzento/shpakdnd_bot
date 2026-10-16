"""Real controlling-terminal regressions; no production commands or data."""
import errno
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import unittest

from tests.v1_3_3 import test_deploy_shell as shell

BASE="5e4d4495a189380542cc643c5c8d723b9351b36b"
LINUX=sys.platform.startswith("linux")


class Terminal:
    """A Linux session leader with a real controlling PTY, and optional fd0 pipe."""
    def __init__(self,harness,*,redirect_stdin=False):
        import pty
        import termios
        self.output=bytearray()
        readfd,writefd=os.pipe() if redirect_stdin else (None,None)
        pid,fd=pty.fork()
        if pid==0:
            try:
                attrs=termios.tcgetattr(0)
                attrs[0]&=~(termios.ICRNL|termios.INLCR|termios.IGNCR)
                attrs[3]&=~termios.ECHO
                termios.tcsetattr(0,termios.TCSANOW,attrs)
                if redirect_stdin:
                    os.close(writefd);os.dup2(readfd,0);os.close(readfd)
                env=dict(harness.env,PATH=str(harness.bin)+os.pathsep+os.environ["PATH"],
                         PYTHONUTF8="1",FAKE_REAL_PYTHON=shell.shell_path(sys.executable,preserve_symlink=True))
                os.execvpe(shell.BASH,[shell.BASH,"--noprofile","--norc",
                                     str(getattr(harness,"script",shell.ROOT/"deploy/deploy-shpakdnd.sh"))],env)
            except BaseException:
                os._exit(127)
        self.pid,self.fd=pid,fd
        self.code=None
        if redirect_stdin:os.close(readfd);os.close(writefd)

    @property
    def text(self):return self.output.decode("utf-8",errors="replace")

    def pump(self,seconds=0.1):
        if self.fd is None:return
        ready,_,_=select.select([self.fd],[],[],seconds)
        if ready:
            try:data=os.read(self.fd,65536)
            except OSError as error:
                if error.errno!=errno.EIO:raise
                data=b""
            if data:self.output.extend(data)
            else:os.close(self.fd);self.fd=None

    def wait_text(self,text,timeout=25):
        end=time.monotonic()+timeout
        while text not in self.text:
            self.pump()
            if time.monotonic()>end or self.fd is None:
                raise AssertionError("Missing visible prompt: "+text+"\n"+self.text)
        return self.text

    def send(self,text):os.write(self.fd,text.encode("utf-8"))

    def finish(self,timeout=30):
        end=time.monotonic()+timeout
        while self.code is None or self.fd is not None:
            self.pump()
            if self.code is None:
                pid,status=os.waitpid(self.pid,os.WNOHANG)
                if pid:self.code=os.waitstatus_to_exitcode(status)
            if time.monotonic()>end:raise AssertionError("PTY did not exit\n"+self.text)
        return self.code

    def close(self):
        if self.code is None:
            try:os.killpg(self.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            try:os.waitpid(self.pid,0)
            except ChildProcessError:pass
            self.code=-signal.SIGKILL
        if self.fd is not None:os.close(self.fd);self.fd=None


class Fixture(unittest.TestCase):
    def setUp(self):
        self.h=shell.DeployShellTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        (self.h.project/".env").write_text("BOT_TOKEN=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde\nFIXTURE_SECRET=hotfix-fixture-private-secret\n",encoding="utf-8")

    def trace(self):return (self.h.state/"trace").read_text(encoding="utf-8")

    def legacy(self):
        deploy=self.h.root/"legacy";deploy.mkdir()
        for name in ("deploy-shpakdnd.sh","deploy_helpers.py","sqlite-deploy.py","telegram-deploy-notice.py","pvp_ops.py"):
            content=subprocess.check_output(["git","show",BASE+":deploy/"+name],cwd=shell.ROOT)
            (deploy/name).write_bytes(content)
        self.h.script=deploy/"deploy-shpakdnd.sh"

    def terminal(self,**flags):
        redirected=flags.pop("redirect_stdin",False)
        self.h.env.update({k:str(v) for k,v in flags.items()})
        terminal=Terminal(self.h,redirect_stdin=redirected);self.addCleanup(terminal.close)
        return terminal

    def first_two(self,t):
        t.wait_text("Обновить production до origin/main? [y/N]:");t.send("y\n")
        t.wait_text("Установить версию "+shell.TARGET[:7]+" из origin/main? [y/N]:");t.send("y\n")

    def assert_started(self):
        trace=self.trace()
        bot="systemctl start shpakdnd-bot.service"
        watcher="systemctl start shpakdnd-bot-watch.path"
        self.assertEqual(trace.count(bot),1,trace);self.assertEqual(trace.count(watcher),1,trace)
        start=trace.index(bot);watch=trace.index(watcher)
        self.assertLess(start,watch)
        between=trace[start:watch]
        self.assertIn("show -p MainPID",between);self.assertIn("show -p NRestarts",between)
        self.assertIn("is-active --quiet shpakdnd-bot.service",between)
        self.assertIn("journalctl",between)


@unittest.skipUnless(LINUX,"Linux controlling PTY required; exercised in both Linux CI jobs")
class LegacyReproductionTests(Fixture):
    def setUp(self):
        super().setUp();self.legacy()

    def test_baseline_three_plain_y_after_long_tests_and_real_redactor(self):
        t=self.terminal(FAKE_TEST_DELAY=3)
        self.first_two(t)
        t.wait_text("FAKE_LONG_TEST_BEGIN");began=time.monotonic()
        t.wait_text("Запустить новую версию? [Y/n]:")
        self.assertIn("FAKE_LONG_TEST_END",t.text)
        self.assertGreaterEqual(time.monotonic()-began,2)
        t.send("y\n")
        self.assertEqual(t.finish(),0,t.text);self.assert_started()
        print("LEGACY_PTY_CONTROL_OK",flush=True)

    def test_baseline_crlf_at_final_prompt_reproduces_stopped_stack(self):
        t=self.terminal(FAKE_TEST_DELAY=3)
        self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("y\r\n")
        self.assertEqual(t.finish(),2,t.text);self.h.assert_stopped()
        self.assertNotIn("systemctl start",self.trace())
        self.assertIn("Проверенный код установлен",t.text)
        print("LEGACY_PTY_CRLF_REPRODUCED: exit=2, services inactive",flush=True)

    def test_baseline_controlling_tty_with_eof_stdin_ignores_terminal(self):
        t=self.terminal(redirect_stdin=True)
        t.wait_text("Обновить production до origin/main? [y/N]:")
        self.assertEqual(t.finish(),0,t.text)
        self.assertIn("Ввод закрыт",t.text)
        self.assertNotIn("systemctl stop",self.trace())
        print("LEGACY_PTY_STDIN_EOF_REPRODUCED",flush=True)
