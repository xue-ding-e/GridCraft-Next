import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cleanup_completed_branches as c

MAIN = '1' * 40

class FakeApi:
    def __init__(self):
        self.refs = {'refs/heads/main': MAIN, **{'refs/heads/' + b: s for b, s, _ in c.TARGETS}}
        self.posts = []
        self.not_ancestor = False
    def request(self, path, method='GET', data=None):
        if method == 'POST':
            self.posts.append(data)
            if data['ref'] in self.refs: raise ValueError('existing ref')
            self.refs[data['ref']] = data['sha']
            return {}
        if '/matching-refs/' in path:
            prefix = 'refs/' + path.split('/matching-refs/')[1]
            return [{'ref': k, 'object': {'type': 'commit', 'sha': v}} for k, v in self.refs.items() if k.startswith(prefix)]
        if '/compare/' in path:
            tip = path.split('/compare/')[1].split('...')[0]
            return {'status': 'diverged' if self.not_ancestor else 'ahead', 'merge_base_commit': {'sha': tip}}
        raise AssertionError(path)

class ScopeTests(unittest.TestCase):
    def test_fixed_scope_and_restore_anchors(self):
        api = FakeApi(); calls = []
        def delete(branch, sha, root):
            calls.append((branch, sha))
            archive = next(a for b, s, a in c.TARGETS if b == branch)
            if archive: self.assertEqual(api.refs['refs/tags/' + archive], sha)
            del api.refs['refs/heads/' + branch]
            return True
        report = c.cleanup(api, MAIN, '.', delete)
        self.assertTrue(report['success']); self.assertEqual(len(calls), 4)
        self.assertEqual(set(api.refs), {'refs/heads/main', *('refs/tags/' + a for _, _, a in c.TARGETS if a)})
        self.assertEqual(len(api.posts), 2)
    def test_absent_diagnostic_branch_without_archive_is_not_claimed_recoverable(self):
        api = FakeApi()
        for b, _, a in c.TARGETS:
            if a: del api.refs['refs/heads/' + b]
        def delete(b,s,r): del api.refs['refs/heads/' + b]; return True
        report = c.cleanup(api, MAIN, '.', delete)
        self.assertFalse(report['success']); self.assertEqual(api.posts, [])
        self.assertTrue(all(r['status'] == 'blocked' for r in report['targets'] if r['archive_tag']))
    def test_absent_refs_require_real_retention_anchors(self):
        api = FakeApi()
        for b, s, a in c.TARGETS:
            del api.refs['refs/heads/' + b]
            if a: api.refs['refs/tags/' + a] = s
        with patch.object(c, 'delete_with_lease') as delete:
            self.assertTrue(c.cleanup(api, MAIN, '.', delete)['success'])
            api.not_ancestor = True
            self.assertFalse(c.cleanup(api, MAIN, '.', delete)['success'])
        delete.assert_not_called(); self.assertEqual(api.posts, [])

    def test_changed_tip_is_never_deleted(self):
        api = FakeApi(); branch = c.TARGETS[0][0]; api.refs['refs/heads/' + branch] = '9' * 40
        calls = []
        def delete(b, s, root): calls.append(b); return False
        report = c.cleanup(api, MAIN, '.', delete)
        self.assertFalse(report['success']); self.assertNotIn(branch, calls)
        self.assertEqual(api.refs['refs/heads/' + branch], '9' * 40)
    def test_archive_conflict_or_non_ancestor_blocks_corresponding_delete(self):
        api = FakeApi(); api.not_ancestor = True
        for _, _, archive in c.TARGETS:
            if archive: api.refs['refs/tags/' + archive] = '9' * 40
        with patch.object(c, 'delete_with_lease') as delete:
            report = c.cleanup(api, MAIN, '.', delete)
        delete.assert_not_called(); self.assertFalse(report['success']); self.assertEqual(api.posts, [])
    def test_archive_readback_failure_blocks_branch(self):
        class BadArchive(FakeApi):
            def request(self, path, method='GET', data=None):
                result = super().request(path, method, data)
                if method == 'POST': self.refs[data['ref']] = '9' * 40
                return result
        api = BadArchive(); calls = []
        def delete(b,s,r): calls.append(b); return False
        c.cleanup(api, MAIN, '.', delete)
        self.assertFalse(any(b.startswith('diagnostic/') for b in calls))
    def test_changed_main_blocks_all_mutations(self):
        api = FakeApi(); api.refs['refs/heads/main'] = '2' * 40
        with patch.object(c, 'delete_with_lease') as delete: report = c.cleanup(api, MAIN, '.', delete)
        delete.assert_not_called(); self.assertEqual(api.posts, []); self.assertFalse(report['success'])
    def test_lost_delete_lease_is_failed_without_retry(self):
        api = FakeApi(); calls = []
        def delete(b, s, root):
            calls.append(b); api.refs['refs/heads/' + b] = '8' * 40; return False
        report = c.cleanup(api, MAIN, '.', delete)
        self.assertFalse(report['success']); self.assertEqual(len(calls), len(set(calls)))
        self.assertTrue(all(api.refs['refs/heads/' + b] == '8' * 40 for b, _, _ in c.TARGETS))
    def test_existing_matching_archive_is_not_rewritten(self):
        api = FakeApi()
        for _, s, a in c.TARGETS:
            if a: api.refs['refs/tags/' + a] = s
        def delete(b,s,r): del api.refs['refs/heads/' + b]; return True
        self.assertTrue(c.cleanup(api, MAIN, '.', delete)['success']); self.assertEqual(api.posts, [])
    def test_auth_is_process_only_and_delete_is_exact_lease(self):
        branch, sha, _ = c.TARGETS[0]
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic-private', 'GIT_TRACE_CURL': '1'}), patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(c.delete_with_lease(branch, sha, '.'))
        args, = run.call_args.args; env = run.call_args.kwargs['env']
        self.assertIn('--force-with-lease=refs/heads/' + branch + ':' + sha, args)
        self.assertEqual(args[-1], ':refs/heads/' + branch)
        self.assertNotIn('synthetic-private', ' '.join(args)); self.assertNotIn('GH_TOKEN', env)
        self.assertNotIn('GIT_TRACE_CURL', env); self.assertEqual(env['GIT_CONFIG_VALUE_2'], 'false')
        with self.assertRaisesRegex(ValueError, 'fixed scope'): c.delete_with_lease('main', MAIN, '.')
    def test_real_git_deletion_lease_rejects_changed_ref(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bare = root / 'bare.git'; work = root / 'work'
            def git(*args): return subprocess.run(['git', *map(str,args)], check=True, capture_output=True, text=True)
            git('init','--bare',bare); git('init',work)
            git('-C',work,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','one')
            old = git('-C',work,'rev-parse','HEAD').stdout.strip()
            git('-C',work,'push',bare,'HEAD:refs/heads/completed')
            git('-C',work,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','two')
            new = git('-C',work,'rev-parse','HEAD').stdout.strip(); git('-C',work,'push',bare,'HEAD:refs/heads/completed')
            bad = subprocess.run(['git','-C',str(work),'push','--force-with-lease=refs/heads/completed:'+old,str(bare),':refs/heads/completed'],capture_output=True)
            self.assertNotEqual(bad.returncode,0); self.assertEqual(git('--git-dir',bare,'rev-parse','refs/heads/completed').stdout.strip(),new)
            git('-C',work,'push','--force-with-lease=refs/heads/completed:'+new,bare,':refs/heads/completed')
            absent = subprocess.run(['git','--git-dir',str(bare),'show-ref','--verify','refs/heads/completed'],capture_output=True)
            self.assertNotEqual(absent.returncode,0)

if __name__ == '__main__': unittest.main()
