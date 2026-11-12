"""Create or update one coding standards findings comment on a pull request."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

MARKER = '<!-- standards-review-action -->'
MAX_COMMENT_CHARS = 60_000


def api(method: str, path: str, token: str, body: dict | None = None) -> object:
    origin = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/')
    if not origin.startswith('https://'):
        raise ValueError('GITHUB_API_URL must use HTTPS')
    request = Request(  # noqa: S310 - HTTPS origin comes from GitHub Actions.
        origin + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - HTTPS origin is validated above.
        return json.load(response)


def existing_comment(repo: str, number: int, token: str, author: str = 'github-actions[bot]') -> int | None:
    page = 1
    while True:
        comments = api('GET', f'/repos/{repo}/issues/{number}/comments?per_page=100&page={page}', token)
        if not isinstance(comments, list):
            raise TypeError('GitHub returned a non-list comments response')
        for comment in comments:
            if (
                isinstance(comment, dict)
                and MARKER in str(comment.get('body', ''))
                and isinstance(comment.get('user'), dict)
                and comment['user'].get('login') == author
            ):
                return int(comment['id'])
        if len(comments) < 100:
            return None
        page += 1


def comment_body(report: Path, run_url: str) -> str:
    if report.is_file():
        findings = report.read_text()
    else:
        findings = (
            '## Standards review\n\n**Could not finish this review.** The job stopped before it wrote a report; '
            'the workflow run has the error. Nothing here means the code passed.\n'
        )
    footer = f'\n<sub>[Workflow run]({run_url})</sub>\n'
    room = MAX_COMMENT_CHARS - len(MARKER) - len(footer) - 80
    if len(findings) > room:
        findings = (
            findings[:room].rsplit('\n', 1)[0] + '\n\nFindings truncated here; the full report is in the check run.\n'
        )
    return f'{MARKER}\n{findings}{footer}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise ValueError('Invalid GITHUB_REPOSITORY')
    number = int(os.environ['PR_NUMBER'])
    if number < 1:
        raise ValueError('Invalid PR_NUMBER')
    token = os.environ['GITHUB_TOKEN']
    run_url = f'{os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")}/{repo}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    body = comment_body(args.report, run_url)
    author = os.environ.get('COMMENT_AUTHOR') or 'github-actions[bot]'
    comment_id = existing_comment(repo, number, token, author)
    if comment_id is None:
        api('POST', f'/repos/{repo}/issues/{number}/comments', token, {'body': body})
        print('Created standards review comment.')
    else:
        api('PATCH', f'/repos/{repo}/issues/comments/{comment_id}', token, {'body': body})
        print(f'Updated standards review comment {comment_id}.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(f'Could not post standards review comment: {error}', file=sys.stderr)
        sys.exit(1)
