"""Full unittest discovery with an explicit fail-closed skipped-test policy."""
import os
from pathlib import Path
import sys
import unittest

PTY_REASON = 'Linux controlling PTY required; exercised in both Linux CI jobs'
WINDOWS_PTY = {
 'ControllingTerminalTests': ('child_read_gets_eof_and_cannot_steal_tty_reply', 'controlling_tty_works_when_stdin_is_an_eof_pipe', 'crlf_for_three_prompts_survives_raw_cr_terminal_settings', 'ctrl_c_while_waiting_is_not_consent', 'failed_start_does_not_start_watcher_and_reads_failure_choice', 'failure_choice_uses_terminal_and_preserves_three_rollback_gates', 'notice_failure_requires_explicit_extra_confirmation', 'sigterm_while_waiting_leaves_stack_stopped', 'terminal_empty_enter_accepts_default_yes', 'terminal_eof_at_launch_is_not_empty_enter', 'terminal_failure_choice_eof_keeps_target_stopped', 'terminal_no_never_starts', 'terminal_unknown_answer_is_not_logged_or_accepted', 'three_y_after_visible_prompts_with_long_tests_and_redactor'),
 'FailureChoiceTerminalTests': ('crlf_two_runs_three_gates_and_stable_old_bot_before_watcher',),
 'LegacyReproductionTests': ('baseline_controlling_tty_with_eof_stdin_ignores_terminal', 'baseline_crlf_at_final_prompt_reproduces_stopped_stack', 'baseline_three_plain_y_after_long_tests_and_real_redactor'),
}


def allowed_skips(platform):
    if platform != 'win32':
        return {}
    result = {'v1_4_3_1.test_deploy_confirm.'+cls+'.test_'+name: PTY_REASON for cls,names in WINDOWS_PTY.items() for name in names}
    result['v1_3_3.test_deploy_shell.ShellPathTests.test_real_posix_executable_symlink_keeps_venv_entry_point'] = 'POSIX symlink semantics'
    return result


class PolicyResult(unittest.TextTestResult):
    def wasSuccessful(self):
        expected = allowed_skips(sys.platform)
        unexpected = [(test,reason) for test,reason in self.skipped if expected.get(test.id()) != reason]
        return super().wasSuccessful() and self.testsRun > 0 and not self.expectedFailures and not unexpected

    def stopTestRun(self):
        super().stopTestRun()
        expected = allowed_skips(sys.platform)
        if any(expected.get(test.id()) != reason for test,reason in self.skipped):
            self.stream.writeln('FAIL: unexplained unittest skip; full diagnostic stays in this test log')


class PolicyRunner(unittest.TextTestRunner):
    resultclass = PolicyResult


if __name__ == '__main__':
    sys.path.insert(0, str(Path.cwd()))
    unittest.main(module=None, argv=['unittest', 'discover', '-s', 'tests', '-q'], testRunner=PolicyRunner)
