#!/usr/bin/env python3
"""Commit the tightened records on github.sha and push it to the default branch."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# GitHub's SSH host keys from GET https://api.github.com/meta, never ssh-keyscan.
HOST_KEYS = (
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl',
    'ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=',
    'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=',
)
MESSAGE = 'ci: tighten comment-ratchet records'
BOT = {f'GIT_{role}_{field}': value for role in ('AUTHOR', 'COMMITTER')
       for field, value in (('NAME', 'github-actions[bot]'),
                            ('EMAIL', '41898282+github-actions[bot]@users.noreply.github.com'))}


def git(root: Path, *args: str, env: dict[str, str] | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                          check=check, timeout=120, env=env)


def records_commit(root: Path, sha: str, records: str) -> str:
    """A commit on sha that changes the records file alone, built without hooks."""
    blob = git(root, 'hash-object', '-w', '--no-filters', '--', records).stdout.strip()
    mode = git(root, 'ls-tree', sha, '--', records).stdout.split()[:1]
    mode = '100755' if mode == ['100755'] else '100644'
    with tempfile.TemporaryDirectory() as directory:
        index = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index')}
        git(root, 'read-tree', sha, env=index)
        git(root, 'update-index', '--add', '--cacheinfo', f'{mode},{blob},{records}', env=index)
        tree = git(root, 'write-tree', env=index).stdout.strip()
    return git(root, 'commit-tree', tree, '-p', sha, '-m', MESSAGE,
               env={**os.environ, **BOT}).stdout.strip()


def main() -> int:
    root = Path(os.environ.get('GITHUB_WORKSPACE', os.getcwd())).resolve()
    names = ('COMMENT_RATCHET_PUSH_KEY', 'COMMENT_RATCHET_RECORDS',
             'COMMENT_RATCHET_DEFAULT_BRANCH', 'GITHUB_SHA', 'GITHUB_REPOSITORY')
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        print(f'comment-ratchet push failed: {", ".join(missing)} unset', file=sys.stderr)
        return 1
    key, records, branch, sha, repository = (os.environ[name] for name in names)
    # No git call, push included, inherits the key; only ssh reads it, from a file.
    del os.environ['COMMENT_RATCHET_PUSH_KEY']
    remote = f'git@github.com:{repository}.git'
    try:
        commit = records_commit(root, sha, records)
    except (OSError, subprocess.SubprocessError) as error:
        print(f'comment-ratchet push failed: {error}', file=sys.stderr)
        return 1
    scratch = Path(tempfile.mkdtemp(dir=os.environ.get('RUNNER_TEMP')))
    try:
        identity, hosts = scratch / 'key', scratch / 'known_hosts'
        with os.fdopen(os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            handle.write(key.strip() + '\n')
        hosts.write_text(''.join(f'github.com {host}\n' for host in HOST_KEYS))
        # GIT_SSH_COMMAND outranks any ambient ssh setting; ssh never sees the key text.
        ssh = {**os.environ, 'GIT_SSH_COMMAND': shlex.join([
            'ssh', '-i', str(identity), '-o', 'IdentitiesOnly=yes',
            '-o', f'UserKnownHostsFile={hosts}', '-o', 'StrictHostKeyChecking=yes'])}
        pushed = git(root, 'push', remote, f'{commit}:refs/heads/{branch}', env=ssh, check=False)
        if pushed.returncode == 0:
            print(f'pushed {commit}, the tightened {records}, to {branch}')
            return 0
        fetched = git(root, 'fetch', remote, f'refs/heads/{branch}', env=ssh, check=False)
    finally:
        shutil.rmtree(scratch)
    refusal = pushed.stderr.strip()
    if fetched.returncode:
        print(f'comment-ratchet push refused: {refusal}; reading {branch} failed: '
              f'{fetched.stderr.strip()}', file=sys.stderr)
        return 1
    tip = git(root, 'rev-parse', 'FETCH_HEAD').stdout.strip()
    contained = git(root, 'merge-base', '--is-ancestor', sha, tip, check=False).returncode == 0
    if not contained:
        print(f'comment-ratchet push refused: {refusal}; {branch} is at {tip}, '
              f'which does not contain {sha}', file=sys.stderr)
        return 1
    if tip != sha:
        # The newer push's own run measures again; a records file is never merged.
        print(f'::notice::{branch} moved past {sha} while this run measured; '
              'nothing pushed, the newer run tightens its own tree')
        return 0
    print(f'comment-ratchet push refused while {branch} stood at {sha}: {refusal}',
          file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
