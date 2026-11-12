"""PR comment behavior without calling GitHub."""

# CI runs these with the Python standard library.
# ruff: noqa: PT009

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import post_comment as subject  # noqa: E402


class CommentTests(unittest.TestCase):
    def test_updates_only_the_existing_bot_comment_across_pages(self):
        human = {'id': 1, 'body': subject.MARKER, 'user': {'login': 'reviewer'}}
        bot = {'id': 42, 'body': subject.MARKER + '\nold', 'user': {'login': 'github-actions[bot]'}}
        with patch.object(subject, 'api', side_effect=[[human] * 100, [bot]]) as api:
            self.assertEqual(subject.existing_comment('org/repo', 7, 'token'), 42)
        self.assertEqual(api.call_count, 2)
        self.assertIn('page=2', api.call_args.args[1])

    def test_main_upserts_one_comment_with_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.md'
            report.write_text('## Scores\n\nOne violation.\n')
            argv = ['comment', '--report', str(report)]
            env = {
                'GITHUB_REPOSITORY': 'org/repo',
                'PR_NUMBER': '7',
                'GITHUB_TOKEN': 'token',
                'GITHUB_RUN_ID': '123',
            }
            with (
                patch.object(sys, 'argv', argv),
                patch.dict(os.environ, env),
                patch.object(subject, 'existing_comment', side_effect=[None, 42]),
                patch.object(subject, 'api') as api,
                patch('sys.stdout', new_callable=io.StringIO),
            ):
                subject.main()
                subject.main()
            self.assertEqual(api.call_args_list[0].args[:2], ('POST', '/repos/org/repo/issues/7/comments'))
            self.assertEqual(api.call_args_list[1].args[:2], ('PATCH', '/repos/org/repo/issues/comments/42'))
            bodies = [item.args[3]['body'] for item in api.call_args_list]
            self.assertEqual(bodies[0], bodies[1])
            self.assertIn(subject.MARKER, bodies[0])
            self.assertIn('One violation.', bodies[0])
            self.assertIn('/org/repo/actions/runs/123', bodies[0])

    def test_failure_and_long_report_remain_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.md'
            missing = subject.comment_body(report, 'https://github.com/run')
            self.assertIn('did not finish', missing)
            report.write_text('## Scores\n' + ('finding\n' * 15_000))
            truncated = subject.comment_body(report, 'https://github.com/run')
            self.assertLessEqual(len(truncated), subject.MAX_COMMENT_CHARS)
            self.assertIn('Findings truncated', truncated)
            self.assertIn('https://github.com/run', truncated)


if __name__ == '__main__':
    unittest.main()
