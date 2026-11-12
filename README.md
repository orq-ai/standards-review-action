# standards-review-action

A GitHub Action that reviews a pull request against your own coding standards file.

You write the rules as a numbered Markdown list. For every file the PR changes, the action sends the diff to a classifier model on the [orq.ai](https://orq.ai) router (default `orq/decider-4b`). It asks one question per rule: `pass`, `violation`, or `not_applicable`. The results go into one PR comment that is edited on every push, plus warning annotations on the flagged files and a job summary.

```
## Coding standards review

Judged 14 files in 14 model calls.

| Rule | Pass | Violations | Not applicable | Score |
| --- | ---: | ---: | ---: | ---: |
| 1. Distinguish absence from failure | 3 | 1 | 10 | 75% |
| 2. Test the behavior behind the claim | 4 | 0 | 10 | 100% |
...
### Violations

- src/fetch.py:42 call 3: rule 1 — Distinguish absence from failure. ...
```

## Standards file format

Any line matching `N. **Title.** Explanation` is a rule. Everything else is ignored. See [`examples/CODING_STANDARDS.md`](examples/CODING_STANDARDS.md).

```markdown
1. **Distinguish absence from failure.** Do not turn a failed call into an empty result.
2. **Test the behavior behind the claim.** A behavior change comes with a test that fails without it.
```

## Usage

```yaml
name: Standards review

on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]

concurrency:
  group: standards-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    # Forks and Dependabot cannot read this repository's secrets.
    if: github.event.pull_request.head.repo.full_name == github.repository && github.actor != 'dependabot[bot]' && !github.event.pull_request.draft
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      # Check out the base, so the standards file comes from trusted code, not the PR.
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          fetch-depth: 1
      - uses: Baukebrenninkmeijer/standards-review-action@v1   # pin to a commit SHA in production
        with:
          api-key: ${{ secrets.ORQ_API_KEY }}
          standards: CODING_STANDARDS.md
```

**Why `pull_request_target` and a base checkout.** The action only reads PR commits as data: it fetches them and diffs them, but never runs them. The scripts come from the action ref and the standards file comes from the base branch, so a PR cannot change the rules it is judged against. A plain `pull_request` trigger also works, but then forks get no secrets, and a PR can edit the standards file it is judged by.

The action diffs from the merge base, the same view as GitHub's "Files changed" tab. It deepens a shallow clone as needed, so `fetch-depth: 1` is enough.

### Inputs

| Input | Default | Description |
| --- | --- | --- |
| `api-key` | required | orq.ai API key with router access |
| `standards` | `CODING_STANDARDS.md` | Standards file path in the checkout |
| `model` | `orq/decider-4b` | Classifier model |
| `endpoint` | `https://api.orq.ai/v3/router/classify` | Classify endpoint (HTTPS only) |
| `ignore` | lockfiles, images, PDFs, CSV/JSONL/Parquet, notebooks, archives, video, fonts | Newline-separated globs; replaces the defaults |
| `extra-ignore` | empty | Newline-separated globs added to the ignore list |
| `max-calls` | `80` | Call budget; files past it are reported as incomplete coverage |
| `fail-on-findings` | `false` | Fail the job on violations or incomplete coverage |
| `comment` | `true` | Create or update one PR comment |
| `comment-author` | `github-actions[bot]` | Login that owns the comment, if you pass another token |
| `github-token` | `github.token` | Token for fetching and commenting |
| `base` / `head` | PR base / head SHA | Commits to compare, for non-PR events |

### Outputs

`violations`, `incomplete`, `exit-code` (0 clean, 1 findings, 2 crash), `report-json`, `report-markdown`. To keep the reports:

```yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: standards-review
          path: ${{ runner.temp }}/standards-review.*
```

## How it judges

- **One call per file.** Each changed file gets its own call, so a violation is tied to its file. A diff over about 26k tokens (78k characters) is split into as few calls as fit. Files never share a call.
- **Advisory by default.** Findings and incomplete coverage leave the job green with a warning. A crash in the script, API, or comment step always fails the job.
- **Fail closed on bad answers.** A missing, extra, or invalid verdict from the model is an error, not a pass.
- **Bounded.** A file's diff is capped at 1 MB, a single diff line must fit in one chunk, and the call budget caps cost. Anything skipped shows up under "Incomplete coverage".
- **Skipped.** Paths that match the ignore globs, and files with binary diffs.
- **Location.** The reported line is where the file's first diff chunk in that call starts, not the exact violating line.

The verdicts help a reviewer decide; they do not replace one. Review a flagged diff in context before changing code.

## Run locally

Python 3.10+, standard library only.

```bash
export ORQ_API_KEY=...
python3 scripts/standards_review.py --base origin/main --head HEAD --standards CODING_STANDARDS.md
python3 scripts/standards_review.py --base origin/main --head HEAD --dry-run   # chunking only, no API calls
python3 -m unittest discover -s tests
```

## License

MIT
