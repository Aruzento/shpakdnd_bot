"""Admission security: real gate logic with paginated GitHub API fixtures, no network."""
import copy
import unittest
from deploy.staging.gate import JOBS, STEPS, REPO, Refused, sha_value, verify

SHA = 'a' * 40


class GateTests(unittest.TestCase):
    def setUp(self):
        self.pull = {'state': 'open', 'base': {'repo': {'full_name': REPO}, 'ref': 'main'},
                     'head': {'repo': {'full_name': REPO}, 'sha': SHA}}
        self.run = {'id': 42, 'run_number': 3, 'run_attempt': 2, 'head_sha': SHA,
                    'event': 'pull_request', 'head_repository': {'full_name': REPO},
                    'workflow_id': 7, 'pull_requests': [{'number': 12}],
                    'status': 'completed', 'conclusion': 'success'}
        self.jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success',
                      'steps': [{'name': step, 'status': 'completed', 'conclusion': 'success'} for step in STEPS]}
                     for name in JOBS]
        self.runs = [self.run]
        self.blob = 'b' * 40
        self.calls = []

    def api(self, path):
        self.calls.append(path)
        if path == 'pulls/12':
            return copy.deepcopy(self.pull)
        if path.startswith('contents/'):
            return {'sha': self.blob if 'ref=main' in path else 'b' * 40}
        if path == 'actions/workflows/linux-release.yml':
            return {'id': 7}
        if '/runs?' in path:
            return {'workflow_runs': self.runs}
        if '/attempts/2/jobs?' in path:
            return {'jobs': self.jobs}
        raise AssertionError(path)

    def test_exact_sha_both_matrix_jobs_and_latest_attempt(self):
        result = verify(self.api, '12', SHA)
        self.assertEqual(result['ci_run'], 42)
        self.assertEqual(result['ci_attempt'], 2)
        self.assertEqual({j['name'] for j in result['jobs']}, JOBS)
        self.assertEqual(self.calls.count('pulls/12'), 2)

    def test_sha_injections_and_ambiguous_refs(self):
        for bad in ('main', SHA[:7], SHA.upper(), SHA + '\n', SHA + ';id', '-', None, ' ' + SHA):
            with self.subTest(bad=bad), self.assertRaises(Refused):
                sha_value(bad)

    def test_fork_closed_wrong_base_stale_head_wrong_repo(self):
        for mutation in ('fork', 'closed', 'base', 'stale', 'repo'):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.pull)
                if mutation == 'fork': self.pull['head']['repo']['full_name'] = 'evil/fork'
                if mutation == 'closed': self.pull['state'] = 'closed'
                if mutation == 'base': self.pull['base']['ref'] = 'dev'
                if mutation == 'stale': self.pull['head']['sha'] = 'c' * 40
                if mutation == 'repo': self.pull['base']['repo']['full_name'] = 'evil/base'
                with self.assertRaises(Refused): verify(self.api, 12, SHA)
                self.pull = original

    def test_missing_pending_failed_cancelled_ci(self):
        self.runs = []
        with self.assertRaises(Refused): verify(self.api, 12, SHA)
        self.runs = [self.run]
        for status, conclusion in [('queued', None), ('in_progress', None), ('completed', 'failure'),
                                   ('completed', 'cancelled'), ('completed', 'neutral')]:
            with self.subTest(status=status, conclusion=conclusion):
                self.run.update(status=status, conclusion=conclusion)
                with self.assertRaises(Refused): verify(self.api, 12, SHA)

    def test_new_failed_run_supersedes_green(self):
        self.runs.append(dict(self.run, id=43, run_number=4, conclusion='failure'))
        with self.assertRaises(Refused): verify(self.api, 12, SHA)

    def test_dispatch_other_pr_and_wrong_sha_are_not_evidence(self):
        for key, value in [('event', 'workflow_dispatch'), ('head_sha', 'c' * 40),
                           ('pull_requests', [{'number': 99}]), ('workflow_id', 88),
                           ('head_repository', {'full_name': 'evil/fork'})]:
            with self.subTest(key=key):
                original = self.run[key]
                self.run[key] = value
                with self.assertRaises(Refused): verify(self.api, 12, SHA)
                self.run[key] = original

    def test_missing_python_job_skipped_step_red_job_modified_workflow(self):
        original = copy.deepcopy(self.jobs)
        self.jobs.pop()
        with self.assertRaises(Refused): verify(self.api, 12, SHA)
        self.jobs = copy.deepcopy(original)
        self.jobs[0]['steps'][0]['conclusion'] = 'skipped'
        with self.assertRaises(Refused): verify(self.api, 12, SHA)
        self.jobs = copy.deepcopy(original)
        self.jobs[0]['conclusion'] = 'failure'
        with self.assertRaises(Refused): verify(self.api, 12, SHA)
        self.jobs = original
        self.blob = 'e' * 40
        with self.assertRaises(Refused): verify(self.api, 12, SHA)

    def test_head_changes_during_collection(self):
        def changed(path):
            result = self.api(path)
            if path == 'pulls/12' and self.calls.count(path) == 2:
                result['head']['sha'] = 'd' * 40
            return result
        with self.assertRaises(Refused): verify(changed, 12, SHA)

    def test_paginated_runs_and_jobs(self):
        from deploy.staging.gate import pages
        calls = []
        def api(path):
            calls.append(path)
            return {'items': list(range(100)) if '&page=1' in path else [101]}
        self.assertEqual(len(pages(api, 'route?x=1', 'items')), 101)
        self.assertEqual(len(calls), 2)
