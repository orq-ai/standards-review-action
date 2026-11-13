"""Contract checks for bounded diff evaluation and fail-closed verdicts."""

# Runs with the Python standard library only: python3 -m unittest discover -s tests
# ruff: noqa: PT009, PT027

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import standards_review as subject  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class StandardsEvalTests(unittest.TestCase):
    def test_chunking_keeps_location_and_bounds_long_hunks(self):
        diff = '@@ -3,4 +3,4 @@\n' + ''.join(f'+new value {index}\n' for index in range(80))
        with patch.object(subject, 'git_bounded', return_value=diff.encode()), patch.object(subject, 'MAX_CHARS', 160):
            chunks = subject.chunks_for('sample.py', 'base', 'head')
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0][0], 3)
        self.assertGreater(chunks[1][0], 3)
        self.assertTrue(all(len(chunk) <= 160 for _, chunk in chunks))
        self.assertIn('+new value 79', chunks[-1][1])

    def test_request_uses_all_rules_and_rejects_missing_answers(self):
        rules = {'1': 'Distinguish absence from failure.', '2': 'Test the behavior behind the claim.'}
        seen = []

        def respond(request, timeout):
            self.assertEqual(timeout, 90)
            seen.append(json.loads(request.data))
            payload = {
                'answers': {
                    'rule_1': {'type': 'choice', 'choice': 'violation'},
                    'rule_2': {'type': 'choice', 'choice': 'not_applicable'},
                }
            }
            return FakeResponse(json.dumps(payload).encode())

        with patch.object(subject, 'urlopen', side_effect=respond):
            verdicts = subject.decide('key', 'sample.py', '+return []\n', rules)
        self.assertEqual(verdicts, {'1': 'violation', '2': 'not_applicable'})
        self.assertEqual(seen[0]['model'], 'orq/decider-4b')
        self.assertEqual(set(seen[0]['questions']), {'rule_1', 'rule_2'})
        self.assertEqual(seen[0]['state']['diff'], '+return []\n')

        with (
            patch.object(subject, 'urlopen', return_value=FakeResponse(b'{"answers": {}}')),
            self.assertRaisesRegex(ValueError, 'missing or unexpected'),
        ):
            subject.decide('key', 'sample.py', '+return []\n', rules)

    def test_binary_marker_is_exact(self):
        with (
            patch.object(subject, 'git_bounded', return_value=b'Binary files a/x and b/x differ\n'),
            self.assertRaises(subject.BinaryDiffError),
        ):
            subject.chunks_for('x', 'base', 'head')
        textual_diff = b'@@ -0,0 +1 @@\n+print("Binary files a/x and b/x differ")\n'
        with patch.object(subject, 'git_bounded', return_value=textual_diff):
            self.assertEqual(len(subject.chunks_for('x.py', 'base', 'head')), 1)

    def test_diff_starts_at_merge_base_when_base_moved_ahead(self):
        def run(*args: str) -> None:
            subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)

        with tempfile.TemporaryDirectory() as repo:
            run('init', '-q', '-b', 'main')
            run('config', 'user.email', 't@example.com')
            run('config', 'user.name', 't')
            (Path(repo) / 'shared.py').write_text('x = 1\n')
            run('add', '.')
            run('commit', '-qm', 'root')
            run('checkout', '-qb', 'feature')
            (Path(repo) / 'feature.py').write_text('y = 2\n')
            run('add', '.')
            run('commit', '-qm', 'feature')
            run('checkout', '-q', 'main')
            (Path(repo) / 'main_only.py').write_text('z = 3\n')
            run('add', '.')
            run('commit', '-qm', 'main moves ahead')
            standards = Path(repo) / 'standards.md'
            standards.write_text('1. **Rule.** Text.\n')
            output = Path(repo) / 'out.json'
            argv = ['standards-review', '--base', 'main', '--head', 'feature', '--standards', str(standards)]
            argv += ['--output', str(output), '--dry-run']
            cwd = Path.cwd()
            os.chdir(repo)
            try:
                with patch.object(sys, 'argv', argv), patch('sys.stdout', new_callable=io.StringIO):
                    self.assertEqual(subject.main(), 0)
            finally:
                os.chdir(cwd)
            rows = json.loads(output.read_text())['results']
            self.assertEqual([row['path'] for row in rows], ['feature.py'])

    def test_chunks_of_one_file_share_a_call_but_files_never_mix(self):
        chunks = [('a.py', 1, 'x' * 40), ('a.py', 9, 'y' * 40), ('a.py', 20, 'z' * 40), ('b.py', 1, 'w')]
        with patch.object(subject, 'MAX_CHARS', 100):
            calls = subject.pack(chunks)
        self.assertEqual([[line for _, line, _ in call] for call in calls], [[1, 9], [20], [1]])
        with patch.object(subject, 'MAX_CHARS', 1000):
            self.assertEqual([[path for path, _, _ in call] for call in subject.pack(chunks)], [['a.py'] * 3, ['b.py']])

    def test_git_output_is_stopped_at_the_limit(self):
        with self.assertRaisesRegex(ValueError, 'exceeds 3 bytes'):
            subject.git_bounded(3, '--version')
        self.assertTrue(subject.git_bounded(100, '--version').startswith(b'git version '))

    def test_standard_parsing_matches_repository_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'CODING_STANDARDS.md'
            path.write_text('# Coding standards\n\n1. **First rule.** Explanation.\n2. **Second rule.** Explanation.\n')
            self.assertEqual(list(subject.rules_from(path)), ['1', '2'])

    def test_summary_escapes_untrusted_filenames(self):
        findings = subject.summary(
            [{'path': '<img src=x onerror=alert(1)>.py', 'line': 2, 'chunk': 1, 'verdicts': {'1': 'violation'}}],
            [],
            [],
            {'1': 'Preserve errors. Do not report failed input as empty.'},
        )
        self.assertNotIn('<img', findings)
        self.assertIn('&lt;img', findings)

    def test_conclusive_violation_fails_check_and_writes_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standards = root / 'CODING_STANDARDS.md'
            standards.write_text('1. **Preserve errors.** Do not report a failed fetch as empty.\n')
            output = root / 'result.json'
            step_summary = root / 'summary.md'
            markdown = root / 'findings.md'
            argv = [
                'standards-review',
                '--base',
                'base',
                '--head',
                'head',
                '--standards',
                str(standards),
                '--output',
                str(output),
                '--markdown-output',
                str(markdown),
            ]
            with (
                patch.object(sys, 'argv', argv),
                patch.dict(os.environ, {'ORQ_API_KEY': 'test-key', 'GITHUB_STEP_SUMMARY': str(step_summary)}),
                patch.object(subject, 'git', return_value=b'base\n'),
                patch.object(subject, 'changed_paths', return_value=['sample.py']),
                patch.object(subject, 'chunks_for', return_value=[(4, '@@ -4 +4 @@\n+return []\n')]),
                patch.object(subject, 'decide', return_value={'1': 'violation'}),
                patch('sys.stdout', new_callable=io.StringIO),
            ):
                self.assertEqual(subject.main(), 1)
            self.assertEqual(json.loads(output.read_text())['results'][0]['verdicts'], {'1': 'violation'})
            self.assertIn('Violations', step_summary.read_text())
            self.assertIn('Violations', markdown.read_text())

    def test_ignore_globs_replace_or_extend_defaults(self):
        self.assertTrue(subject.is_ignored('deep/dir/uv.lock', list(subject.DEFAULT_IGNORE)))
        self.assertFalse(subject.is_ignored('src/app.py', list(subject.DEFAULT_IGNORE)))
        self.assertTrue(subject.is_ignored('docs/generated/index.md', ['docs/generated/*']))

        def run(*extra: str) -> list[str]:
            argv = ['--base', 'b', '--head', 'h', '--standards', str(standards), '--output', str(output), '--dry-run']
            with (
                patch.object(subject, 'git', return_value=b'base\n'),
                patch.object(subject, 'changed_paths', return_value=['uv.lock', 'gen/a.md', 'app.py']),
                patch.object(subject, 'chunks_for', return_value=[(1, '@@ -1 +1 @@\n+x\n')]),
                patch('sys.stdout', new_callable=io.StringIO),
            ):
                subject.main([*argv, *extra])
            return json.loads(output.read_text())['ignored']

        with tempfile.TemporaryDirectory() as directory:
            standards = Path(directory) / 'standards.md'
            standards.write_text('1. **Rule.** Text.\n')
            output = Path(directory) / 'out.json'
            self.assertEqual(run(), ['uv.lock'])
            self.assertEqual(run('--extra-ignore', 'gen/*'), ['uv.lock', 'gen/a.md'])
            self.assertEqual(run('--ignore', 'gen/*'), ['gen/a.md'])

    def test_call_budget_marks_incomplete_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standards = root / 'standards.md'
            standards.write_text('1. **Rule.** Text.\n')
            github_output = root / 'github_output'
            argv = ['--base', 'b', '--head', 'h', '--standards', str(standards), '--output', str(root / 'o.json')]
            argv += ['--markdown-output', str(root / 'o.md'), '--max-calls', '1', '--model', 'org/other-model']
            with (
                patch.dict(os.environ, {'ORQ_API_KEY': 'k', 'GITHUB_OUTPUT': str(github_output)}),
                patch.object(subject, 'git', return_value=b'base\n'),
                patch.object(subject, 'changed_paths', return_value=['a.py', 'b.py']),
                patch.object(subject, 'chunks_for', return_value=[(1, '@@ -1 +1 @@\n+x\n')]),
                patch.object(subject, 'decide', return_value={'1': 'pass'}) as decide,
                patch('sys.stdout', new_callable=io.StringIO),
            ):
                self.assertEqual(subject.main(argv), 1)
            self.assertEqual(decide.call_count, 1)
            self.assertEqual(decide.call_args.args[4], 'org/other-model')
            self.assertEqual(github_output.read_text(), 'violations=0\nincomplete=1\n')
            self.assertIn('2 calls exceed the 1-call budget', (root / 'o.md').read_text())

    def test_endpoint_must_be_https(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            subject.decide('k', 'a.py', '+x\n', {'1': 'Rule.'}, endpoint='http://example.com/decisions')

    def test_headline_says_what_to_do_first(self):
        rules = {'1': 'Rule one.', '2': 'Rule two.'}
        clean = [{'path': 'a.py', 'line': 1, 'chunk': 1, 'verdicts': {'1': 'pass', '2': 'not_applicable'}}]
        flagged = [
            {'path': 'a.py', 'line': 1, 'chunk': 1, 'verdicts': {'1': 'violation', '2': 'violation'}},
            {'path': 'b.py', 'line': 1, 'chunk': 2, 'verdicts': {'1': 'pass', '2': 'pass'}},
        ]
        missing = [{'path': 'big.html', 'reason': 'one diff line exceeds chunk limit'}]
        self.assertIn('**All clear.** 1 file read against 2 rules', subject.headline(clean, [], rules))
        self.assertIn('**2 things worth a second look** in 1 file.', subject.headline(flagged, [], rules))
        self.assertIn('1 item could not be read', subject.headline(flagged, missing, rules))
        self.assertIn('Nothing flagged in what I could read', subject.headline(clean, missing, rules))
        self.assertIn('Nothing to judge', subject.headline([], [], rules))
        report = subject.summary(flagged, [], [], rules)
        self.assertLess(report.index('worth a second look'), report.index('| Rule |'))

    def test_crash_leaves_a_report_that_does_not_read_as_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            standards = root / 'standards.md'
            standards.write_text('1. **Rule.** Text.\n')
            markdown = root / 'o.md'
            argv = ['--base', 'b', '--head', 'h', '--standards', str(standards), '--output', str(root / 'o.json')]
            argv += ['--markdown-output', str(markdown)]
            boom = RuntimeError('decisions API HTTP 403: <denied>')
            with (
                patch.dict(os.environ, {'ORQ_API_KEY': 'k'}),
                patch.object(subject, 'git', return_value=b'base\n'),
                patch.object(subject, 'changed_paths', return_value=['a.py']),
                patch.object(subject, 'chunks_for', return_value=[(1, '@@ -1 +1 @@\n+x\n')]),
                patch.object(subject, 'decide', side_effect=boom),
                patch('sys.stdout', new_callable=io.StringIO),
                self.assertRaises(RuntimeError),
            ):
                subject.main(argv)
            text = markdown.read_text()
            self.assertIn('Could not finish this review', text)
            self.assertIn('&lt;denied&gt;', text)
            self.assertNotIn('All clear', text)

    def test_permission_error_names_the_missing_permission(self):
        from urllib.error import HTTPError

        error = HTTPError('https://x', 403, 'Forbidden', {}, io.BytesIO(b'{"error": "Missing permission"}'))
        with (
            patch.object(subject, 'urlopen', side_effect=error),
            self.assertRaisesRegex(RuntimeError, 'classify.execute'),
        ):
            subject.decide('k', 'a.py', '+x\n', {'1': 'Rule.'})


if __name__ == '__main__':
    unittest.main()
