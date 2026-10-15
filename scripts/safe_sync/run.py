#!/usr/bin/env python3
"""Trusted PR publisher; only a fixed branch in the user's repository is writable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = 'intilm888-afk/biz-ai-video-course'
BRANCH = 'sync/upstream-main'
MARKER = '<!-- safe-upstream-sync:v1 -->'


def run(*args, capture=False, **kwargs):
    result = subprocess.run(args, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, **kwargs)
    return result.stdout.strip() if capture else None


def git(*args, **kwargs):
    return run('git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', *args, **kwargs)


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Only this repository and main workflow invocation are allowed')
    os.environ['GH_REPO'] = REPO
    prs = json.loads(run('gh', 'pr', 'list', '--state', 'open', '--base', 'main',
                         '--head', BRANCH, '--json', 'number,body,author', capture=True))
    if len(prs) > 1:
        raise RuntimeError('Multiple sync PRs found; resolve manually')
    if prs and (MARKER not in prs[0]['body'] or prs[0]['author']['login'] not in
                ('app/github-actions', 'github-actions[bot]')):
        raise RuntimeError('Existing PR is not owned by this automation; stop safely')
    # Token stays in the credential helper process environment, never persisted.
    git('config', 'credential.helper', '')
    git('config', '--add', 'credential.helper',
        '!f() { test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; }; f')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    try:
        git('fetch', 'origin', 'main')
        remote_branch = git('ls-remote', '--heads', 'origin', f'refs/heads/{BRANCH}', capture=True)
        if remote_branch:
            if not prs:
                previous = json.loads(run('gh', 'pr', 'list', '--state', 'all', '--head', BRANCH,
                                          '--json', 'state,body', '--limit', '100', capture=True))
                if not previous or previous[0]['state'] != 'MERGED' or MARKER not in previous[0]['body']:
                    raise RuntimeError('Orphan/closed sync branch exists; inspect it before rerunning')
            git('fetch', 'origin', f'refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}')
            git('switch', '-c', BRANCH, f'origin/{BRANCH}')
            try:
                git('merge', '--no-edit', 'origin/main')
            except subprocess.CalledProcessError:
                conflicts = git('diff', '--name-only', '--diff-filter=U', capture=True)
                git('merge', '--abort')
                raise RuntimeError('main/PR merge conflicts; no push performed:\n' + conflicts) from None
        else:
            if prs:
                raise RuntimeError('Open sync PR branch is missing')
            git('switch', '-c', BRANCH, 'origin/main')
        # Explicit public fetch, credential helper disabled for the teacher.
        git('-c', 'credential.helper=', 'fetch', '--no-tags',
            'https://github.com/Kouzui-HUANG/biz-ai-video-course.git',
            '+refs/heads/main:refs/remotes/teacher/main')
        child_env = os.environ.copy()
        child_env.pop('GH_TOKEN', None)
        child_env.pop('GITHUB_TOKEN', None)
        run(sys.executable, str(Path(__file__).with_name('sync.py')), env=child_env)
        report = Path('.safe-sync/report.md').read_text()
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as output:
                output.write(report)
        git('add', '--all')
        if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode != 0:
            git('commit', '-m', 'chore: safely synchronize teacher main and Codex skills')
        if not git('diff', '--name-only', 'origin/main...HEAD', capture=True):
            print('No changes relative to main; no empty PR created.')
            return
        # A normal push rejects concurrent remote changes rather than overwriting.
        git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
        with tempfile.NamedTemporaryFile('w', suffix='.md') as body:
            body.write(MARKER + '\n\n' + report +
                       '\n\nReview all changes, conflicts and validation errors before manually merging.\n')
            body.flush()
            if prs:
                # Only replace the machine-owned body; human commits/comments remain.
                run('gh', 'pr', 'edit', str(prs[0]['number']), '--body-file', body.name)
            else:
                try:
                    run('gh', 'pr', 'create', '--draft', '--base', 'main', '--head', BRANCH,
                        '--title', 'chore: review safe upstream synchronization', '--body-file', body.name)
                except subprocess.CalledProcessError:
                    url = f'https://github.com/{REPO}/compare/main...{BRANCH}?expand=1'
                    message = ('PR creation denied. Branch is preserved. Enable “Allow GitHub Actions to '
                               'create and approve pull requests” or manually open: ' + url)
                    print(message)
                    if summary:
                        with open(summary, 'a') as output:
                            output.write('\n\n' + message + '\n')
                    raise
    finally:
        git('config', '--unset-all', 'credential.helper')


if __name__ == '__main__':
    main()
