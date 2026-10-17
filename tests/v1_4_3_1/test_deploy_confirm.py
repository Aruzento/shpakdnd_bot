"""Real controlling-terminal regressions; no production commands or data."""
import errno
from contextlib import closing
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import time
import unittest

from tests.v1_3_3 import test_deploy_shell as shell

BASE="5e4d4495a189380542cc643c5c8d723b9351b36b"
LINUX=sys.platform.startswith("linux")


class Terminal:
    """A Linux session leader with a real controlling PTY, and optional redirected EOF stdin."""
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
                    os.close(writefd)
                    os.dup2(readfd,0)
                    if readfd!=0:os.close(readfd)
                env=dict(harness.env,PATH=str(harness.bin)+os.pathsep+os.environ["PATH"],
                         PYTHONUTF8="1",FAKE_REAL_PYTHON=shell.shell_path(sys.executable,preserve_symlink=True))
                os.execvpe(shell.BASH,[shell.BASH,"--noprofile","--norc",
                                     str(getattr(harness,"script",shell.ROOT/"deploy/deploy-shpakdnd.sh"))],env)
            except BaseException as error:
                os.write(2,("PTY child setup failed: "+type(error).__name__+"\n").encode())
                os._exit(127)
        self.pid,self.fd=pid,fd
        self.code=None
        self.input_writer=writefd
        if redirect_stdin:os.close(readfd)

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

    def eof_stdin(self):
        if self.input_writer is not None:
            os.close(self.input_writer);self.input_writer=None

    def close(self):
        self.eof_stdin()
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
        self.h.env.update(FAKE_REAL_PYTHON=shell.shell_path(sys.executable,preserve_symlink=True),FAKE_GUARD_REAL="1")
        database=self.h.project/"shpakdnd.db"
        database.unlink()  # Only the fake byte fixture created above, under TemporaryDirectory.
        from app.mini.schema import init_mini_db
        init_mini_db(database)
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("UPDATE mini_event_pvp_control SET paused=1")
            conn.commit()
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

    def first_two(self,t,reply="y\n"):
        t.wait_text("Обновить production до origin/main? [y/N]:");t.send(reply)
        t.wait_text("Установить версию "+shell.TARGET[:7]+" из origin/main? [y/N]:");t.send(reply)

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
        t.send("y\n")  # An answer on the controlling terminal is ignored by old fd0 read.
        t.eof_stdin()
        self.assertEqual(t.finish(),0,t.text)
        self.assertNotIn("systemctl stop",self.trace())
        print("LEGACY_PTY_STDIN_EOF_REPRODUCED",flush=True)

class PipeInputTests(Fixture):
    def run_input(self,answers,**flags):
        return self.h.run_deploy(answers,**flags)

    def test_pipe_child_cannot_consume_final_reply(self):
        result,trace=self.run_input("y\ny\ny\n",FAKE_READ_STDIN=1,FAKE_TEST_DELAY=1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("stdin_eof",trace);self.assertNotIn("stdin_stolen",trace)
        self.assert_started()

    def test_pipe_yes_tokens_and_surrounding_whitespace(self):
        for token in ("y","Y","yes","д","Д"," \ty\r \t"):
            with self.subTest(token=token):
                # Reset only our fake command state between independent deployments.
                for unit in ("shpakdnd-bot.service","shpakdnd-bot-watch.path"):
                    (self.h.state/unit).write_text("active\n")
                (self.h.state/"head").write_text(shell.OLD+"\n")
                (self.h.state/"notices").write_text("0\n")
                (self.h.state/"trace").unlink(missing_ok=True)
                result,trace=self.run_input("y\ny\n"+token+"\n")
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assert_started()

    def test_pipe_crlf_for_all_three_prompts(self):
        result,trace=self.run_input("y\r\ny\r\ny\r\n",FAKE_TEST_DELAY=1)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assert_started()

    def test_pipe_eof_and_unterminated_y_do_not_start(self):
        for suffix in ("","y"):
            with self.subTest(suffix=suffix):
                (self.h.state/"head").write_text(shell.OLD+"\n")
                (self.h.state/"trace").unlink(missing_ok=True)
                result,trace=self.run_input("y\ny\n"+suffix)
                self.assertEqual(result.returncode,2,result.stdout+result.stderr)
                self.assertIn("confirm EOF",result.stdout)
                self.assertNotIn("systemctl start",trace);self.h.assert_stopped()

    def test_pipe_no_or_unknown_does_not_start_or_echo_answer(self):
        for answer in ("n","N","unrecognized-private-answer"):
            with self.subTest(answer=answer):
                (self.h.state/"head").write_text(shell.OLD+"\n")
                (self.h.state/"trace").unlink(missing_ok=True)
                result,trace=self.run_input("y\ny\n"+answer+"\n")
                self.assertEqual(result.returncode,2,result.stdout+result.stderr)
                self.assertIn("confirm declined",result.stdout)
                self.assertNotIn("systemctl start",trace);self.h.assert_stopped()
                if answer.startswith("unrecognized"):self.assertNotIn(answer,result.stdout)

    def test_pipe_empty_enter_accepts_only_default_yes(self):
        result,trace=self.run_input("y\ny\n\n")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assert_started()

    def test_pipe_initial_eof_or_empty_default_no_never_stops(self):
        for answers in ("","\n"):
            with self.subTest(answers=answers):
                result,trace=self.run_input(answers)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertNotIn("systemctl stop",trace)

    def test_pipe_redactor_and_confirm_log_never_expose_secret_or_answer(self):
        answer="unrecognized-private-answer"
        result,trace=self.run_input("y\ny\n"+answer+"\n",FAKE_EMIT_SECRET=1)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        logs="".join(p.read_text(encoding="utf-8") for p in (self.h.root/"logs").glob("*.log"))
        for text in (result.stdout,logs):
            self.assertNotIn(answer,text)
            self.assertNotIn("hotfix-fixture-private-secret",text)
            self.assertNotIn("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde",text)
            self.assertIn("FAKE_OUTPUT [REDACTED] [REDACTED]",text)
            self.assertIn("confirm declined",text)

    def test_read_descriptor_error_cannot_accept_or_start(self):
        deploy=self.h.root/"bad-read";deploy.mkdir()
        for name in ("deploy-shpakdnd.sh","deploy_helpers.py","sqlite-deploy.py","telegram-deploy-notice.py","pvp_ops.py"):
            content=(shell.ROOT/"deploy"/name).read_bytes()
            if name=="deploy-shpakdnd.sh":
                text=content.decode("utf-8")
                marker="    if ! confirm 'Запустить новую версию?' Y; then"
                assert marker in text
                content=text.replace(marker,"    DEPLOY_INPUT_FD=9999\n"+marker).encode("utf-8")
            (deploy/name).write_bytes(content)
        self.h.script=deploy/"deploy-shpakdnd.sh"
        result,trace=self.run_input("y\ny\ny\n")
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn("confirm EOF",result.stdout)
        self.assertNotIn("systemctl start",trace);self.h.assert_stopped()

    def test_safety_guards_and_frozen_helpers_match_base(self):
        original=subprocess.check_output(["git","show",BASE+":deploy/deploy-shpakdnd.sh"],cwd=shell.ROOT).decode()
        current=(shell.ROOT/"deploy/deploy-shpakdnd.sh").read_text(encoding="utf-8")
        def function(text,name):
            lines=text.split(name+"() {",1)[1].splitlines()
            return lines[:lines.index("}")]
        for name in ("rollback","rollback_preflight","start_stack","stop_stack","checks","checkout_head","on_exit"):
            self.assertEqual(function(original,name),function(current,name),name)
        for name in ("deploy_helpers.py","sqlite-deploy.py","telegram-deploy-notice.py","pvp_ops.py","install-deploy-shpakdnd.sh"):
            before=subprocess.check_output(["git","show",BASE+":deploy/"+name],cwd=shell.ROOT).decode()
            self.assertEqual(before,(shell.ROOT/"deploy"/name).read_text(encoding="utf-8"),name)

    def test_pid_change_cannot_start_watcher(self):
        result,trace=self.run_input("y\ny\ny\n1\n",FAKE_PID_CHANGE=1)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertNotIn("systemctl start shpakdnd-bot-watch.path",trace)
        self.h.assert_stopped()


@unittest.skipUnless(LINUX,"Linux controlling PTY required; exercised in both Linux CI jobs")
class ControllingTerminalTests(Fixture):
    def success(self,*,reply="y\n",redirect=False,**flags):
        t=self.terminal(redirect_stdin=redirect,FAKE_TEST_DELAY=3,**flags)
        self.first_two(t,reply=reply)
        if redirect:t.eof_stdin()
        t.wait_text("FAKE_LONG_TEST_BEGIN")
        t.wait_text("Запустить новую версию? [Y/n]:")
        self.assertIn("FAKE_LONG_TEST_END",t.text)
        t.send(reply)
        self.assertEqual(t.finish(),0,t.text);self.assert_started()
        return t

    def test_three_y_after_visible_prompts_with_long_tests_and_redactor(self):
        self.success()

    def test_crlf_for_three_prompts_survives_raw_cr_terminal_settings(self):
        self.success(reply="y\r\n")

    def test_controlling_tty_works_when_stdin_is_an_eof_pipe(self):
        self.success(redirect=True,reply="y\r\n")

    def test_child_read_gets_eof_and_cannot_steal_tty_reply(self):
        self.success(FAKE_READ_STDIN=1)
        self.assertIn("stdin_eof",self.trace())
        self.assertNotIn("stdin_timeout",self.trace())
        self.assertNotIn("stdin_stolen",self.trace())

    def test_terminal_eof_at_launch_is_not_empty_enter(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("\x04")
        self.assertEqual(t.finish(),2,t.text);self.h.assert_stopped()
        self.assertIn("confirm EOF",t.text);self.assertNotIn("systemctl start",self.trace())

    def test_terminal_unknown_answer_is_not_logged_or_accepted(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("unrecognized-private-answer\n")
        self.assertEqual(t.finish(),2,t.text);self.h.assert_stopped()
        logs="".join(p.read_text(encoding="utf-8") for p in (self.h.root/"logs").glob("*.log"))
        self.assertNotIn("unrecognized-private-answer",logs)
        self.assertIn("confirm declined",logs)

    def test_terminal_no_never_starts(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("N\n")
        self.assertEqual(t.finish(),2,t.text);self.h.assert_stopped()
        self.assertNotIn("systemctl start",self.trace())

    def test_terminal_empty_enter_accepts_default_yes(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("\n")
        self.assertEqual(t.finish(),0,t.text);self.assert_started()

    def test_sigterm_while_waiting_leaves_stack_stopped(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");os.kill(t.pid,signal.SIGTERM)
        self.assertEqual(t.finish(),143,t.text);self.h.assert_stopped()
        self.assertNotIn("systemctl start",self.trace())

    def test_ctrl_c_while_waiting_is_not_consent(self):
        t=self.terminal();self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send(chr(3))
        self.assertEqual(t.finish(),130,t.text);self.h.assert_stopped()
        self.assertIn("Deployment прерван",t.text)
        self.assertNotIn("KeyboardInterrupt",t.text)
        self.assertNotIn("systemctl start",self.trace())

    def test_terminal_failure_choice_eof_keeps_target_stopped(self):
        t=self.terminal(FAKE_CHECK_FAIL=1);self.first_two(t)
        t.wait_text("Выбор [1]:");t.send(chr(4))
        self.assertEqual(t.finish(),1,t.text);self.h.assert_stopped()
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.TARGET)
        self.assertNotIn("systemctl start",self.trace())

    def test_failed_start_does_not_start_watcher_and_reads_failure_choice(self):
        t=self.terminal(FAKE_START_FAIL=1);self.first_two(t)
        t.wait_text("Запустить новую версию? [Y/n]:");t.send("y\n")
        t.wait_text("Выбор [1]:");t.send("1\n")
        self.assertEqual(t.finish(),1,t.text);self.h.assert_stopped()
        self.assertNotIn("systemctl start shpakdnd-bot-watch.path",self.trace())

    def test_failure_choice_uses_terminal_and_preserves_three_rollback_gates(self):
        t=self.terminal(FAKE_CHECK_FAIL=1);self.first_two(t)
        t.wait_text("Выбор [1]:");t.send("2\n")
        self.assertEqual(t.finish(),1,t.text);self.assert_started()
        trace=self.trace();self.assertEqual(trace.count("pvp_ops.py"),3,trace)
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.OLD)

    def test_notice_failure_requires_explicit_extra_confirmation(self):
        t=self.terminal(FAKE_NOTICE_FAIL=1)
        t.wait_text("Обновить production до origin/main? [y/N]:");t.send("y\n")
        t.wait_text("Продолжить deployment? [y/N]:");t.send("n\n")
        self.assertEqual(t.finish(),0,t.text)
        self.assertNotIn("systemctl stop",self.trace())


class FailureChoicePipeTests(Fixture):
    def run_choice(self,reply):
        return self.h.run_deploy("y\ny\n"+reply,FAKE_CHECK_FAIL=1)

    def assert_database_unchanged(self,before):
        database=self.h.project/"shpakdnd.db"
        self.assertEqual(database.read_bytes(),before)
        with closing(sqlite3.connect(database)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(),[("ok",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[])

    def assert_rollback(self,reply):
        before=(self.h.project/"shpakdnd.db").read_bytes()
        result,trace=self.run_choice(reply)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.OLD)
        self.assertEqual(trace.count("pvp_ops.py"),3,trace)
        self.assertEqual(trace.count(" backup "),1,trace)
        self.assert_started()
        self.assert_database_unchanged(before)

    def test_lf_two_selects_guarded_rollback(self):
        self.assert_rollback("2\n")

    def test_crlf_two_selects_guarded_rollback(self):
        self.assert_rollback("2\r\n")

    def test_ascii_space_tab_crlf_two_selects_guarded_rollback(self):
        self.assert_rollback(" \t2 \t\r\n")

    def test_invalid_empty_eof_and_partial_two_keep_target_stopped(self):
        before=(self.h.project/"shpakdnd.db").read_bytes()
        for reply in ("1\n","1\r\n","\n","","2","yes\n","y\n","3\n","22\n","\u00a02\r\n"):
            with self.subTest(reply=reply):
                # Reset only the disposable fake command state between deployments.
                for unit in ("shpakdnd-bot.service","shpakdnd-bot-watch.path"):
                    (self.h.state/unit).write_text("active\n")
                (self.h.state/"head").write_text(shell.OLD+"\n")
                (self.h.state/"notices").write_text("0\n")
                (self.h.state/"trace").unlink(missing_ok=True)
                result,trace=self.run_choice(reply)
                self.assertEqual(result.returncode,1,result.stdout+result.stderr)
                self.assertEqual((self.h.state/"head").read_text().strip(),shell.TARGET)
                self.assertIn("Бот оставлен остановленным",result.stdout)
                self.assertNotIn("pvp_ops.py",trace)
                self.assertNotIn("systemctl start",trace)
                self.h.assert_stopped()
                self.assert_database_unchanged(before)

    def test_read_error_does_not_authorize_rollback(self):
        deploy=self.h.root/"bad-choice-read";deploy.mkdir()
        for name in ("deploy-shpakdnd.sh","deploy_helpers.py","sqlite-deploy.py","telegram-deploy-notice.py","pvp_ops.py"):
            content=(shell.ROOT/"deploy"/name).read_bytes()
            if name=="deploy-shpakdnd.sh":
                text=content.decode("utf-8")
                marker="    read_input choice || choice=1"
                self.assertEqual(text.count(marker),1)
                content=text.replace(marker,"    DEPLOY_INPUT_FD=9999\n"+marker).encode("utf-8")
            (deploy/name).write_bytes(content)
        self.h.script=deploy/"deploy-shpakdnd.sh"
        before=(self.h.project/"shpakdnd.db").read_bytes()
        result,trace=self.run_choice("2\r\n")
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn("Бот оставлен остановленным",result.stdout)
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.TARGET)
        self.assertNotIn("pvp_ops.py",trace)
        self.assertNotIn("systemctl start",trace);self.h.assert_stopped()
        self.assert_database_unchanged(before)

    def test_crlf_two_cannot_bypass_real_pause_new_gate(self):
        database=self.h.project/"shpakdnd.db"
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("UPDATE mini_event_pvp_control SET paused=0");conn.commit()
        before=database.read_bytes()
        result,trace=self.run_choice("2\r\n")
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertIn("pause_new_required",result.stdout)
        self.assertIn("Rollback не завершён",result.stdout)
        self.assertEqual(trace.count("pvp_ops.py"),1,trace)
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.TARGET)
        self.assertNotIn("systemctl start",trace);self.h.assert_stopped()
        self.assert_database_unchanged(before)


@unittest.skipUnless(LINUX,"Linux controlling PTY required; exercised in both Linux CI jobs")
class FailureChoiceTerminalTests(Fixture):
    def test_crlf_two_runs_three_gates_and_stable_old_bot_before_watcher(self):
        database=self.h.project/"shpakdnd.db"
        before=database.read_bytes()
        t=self.terminal(FAKE_CHECK_FAIL=1);self.first_two(t)
        t.wait_text("Выбор [1]:");t.send("2\r\n")
        self.assertEqual(t.finish(),1,t.text)
        trace=self.trace()
        self.assertEqual(trace.count("pvp_ops.py"),3,trace)
        self.assertEqual((self.h.state/"head").read_text().strip(),shell.OLD)
        self.assert_started()
        # Gates bracket the old checkout/checks; all finish before starting bot.
        gates=[i for i,line in enumerate(trace.splitlines()) if "pvp_ops.py" in line]
        lines=trace.splitlines()
        checkout=next(i for i,line in enumerate(lines) if " switch " in line and line.endswith(shell.OLD))
        old_check=next(i for i,line in enumerate(lines) if i>checkout and "check_bot.py" in line)
        bot=next(i for i,line in enumerate(lines) if line=="systemctl start shpakdnd-bot.service")
        self.assertLess(gates[0],gates[1]);self.assertLess(gates[1],checkout)
        self.assertLess(checkout,old_check);self.assertLess(old_check,gates[2])
        self.assertLess(gates[2],bot)
        self.assertEqual(trace.count(" backup "),1,trace)
        self.assertEqual(database.read_bytes(),before)  # No automatic backup restoration.
        with closing(sqlite3.connect(database)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(),[("ok",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[])
        print("FAILURE_CHOICE_PTY_CRLF_ROLLBACK_OK: gates=3, old HEAD, stable bot before watcher, SQLite unchanged",flush=True)
