"""A default-branch push commits its own tightened records."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_ci_comment_baseline import commit, git, init_repo, write_records

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))

ROOT = Path(__file__).resolve().parents[1]
RECORDS = '.github/comment-share.json'
# Records each call, then serves git@github.com:owner/consumer.git from the
# local bare repository.
FAKE_SSH = '''#!{python}
import json, os, shlex, stat, subprocess, sys
args = sys.argv[1:]
options = [args[at + 1] for at, arg in enumerate(args) if arg == '-o']
identity = args[args.index('-i') + 1]
hosts = next(option.split('=', 1)[1] for option in options
             if option.startswith('UserKnownHostsFile='))
verb, path = shlex.split(args[-1])
with open(os.environ['FAKE_SSH_LOG'], 'a') as log:
    log.write(json.dumps({{
        'host': args[-2], 'path': path, 'options': options,
        'mode': stat.S_IMODE(os.stat(identity).st_mode),
        'key': open(identity).read(), 'hosts': open(hosts).read(),
        'key_in_env': 'COMMENT_RATCHET_PUSH_KEY' in os.environ}}) + '\\n')
if os.environ.get('FAKE_SSH_REFUSE') == verb:
    sys.exit(255)
sys.exit(subprocess.call(['git', verb.removeprefix('git-'), os.environ['FAKE_SSH_REPO']]))
'''


@pytest.fixture
def consumer(tmp_path):
    """A checkout of a default branch whose x.py record [4, 6] tighten lowers to [3, 6]."""
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    repo = tmp_path / 'checkout'
    repo.mkdir()
    init_repo(repo)
    git(repo, 'checkout', '-q', '-b', 'main')
    (repo / 'x.py').write_text('# hi\nx = 1\n')
    (repo / 'notes.txt').write_text('kept\n')
    write_records(repo, {'x.py': [4, 6]})
    sha = commit(repo, 'default branch tip')
    git(repo, 'push', '-q', str(origin), 'main')
    git(repo, 'checkout', '-q', '--detach', sha)
    (tmp_path / 'runner').mkdir()
    (tmp_path / 'bin').mkdir()
    ssh = tmp_path / 'bin' / 'ssh'
    ssh.write_text(FAKE_SSH.format(python=sys.executable))
    ssh.chmod(0o755)
    return repo, origin, sha


def ssh_calls(repo):
    log = repo.parent / 'ssh.log'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def run_action(repo, sha, key='test-key', **overrides):
    """run.py, then the push step when the measuring step reports a change."""
    runner = repo.parent / 'runner'
    output = runner.parent / 'output'
    output.write_text('')
    env = dict(os.environ, GITHUB_WORKSPACE=str(repo), GITHUB_EVENT_NAME='push',
               GITHUB_REF='refs/heads/main', GITHUB_SHA=sha,
               GITHUB_REPOSITORY='owner/consumer', GITHUB_OUTPUT=str(output),
               RUNNER_TEMP=str(runner), COMMENT_RATCHET_DEFAULT_BRANCH='main',
               COMMENT_RATCHET_MODE='auto', COMMENT_RATCHET_RECORDS=RECORDS,
               COMMENT_RATCHET_PUSH_KEY=key,
               COMMENT_RATCHET_PUSH_KEY_SET=str(bool(key)).lower(),
               PATH=f'{repo.parent / "bin"}{os.pathsep}{os.environ["PATH"]}',
               FAKE_SSH_LOG=str(repo.parent / 'ssh.log'),
               FAKE_SSH_REPO=str(repo.parent / 'origin.git'))
    env.update(overrides)
    measured = subprocess.run([sys.executable, '-I', str(ROOT / 'run.py')], cwd=repo, env=env,
                              capture_output=True, text=True, check=False)
    if measured.returncode or output.read_text() != 'changed=true\n':
        return measured, None
    pushed = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/push_records.py')],
                            cwd=repo, env=env, capture_output=True, text=True, check=False)
    return measured, pushed


def tip(origin):
    return git(origin, 'rev-parse', 'refs/heads/main')


def test_the_key_reaches_only_the_gated_push_step():
    steps = yaml.safe_load((ROOT / 'action.yml').read_text())['runs']['steps']
    measuring = next(step for step in steps if step.get('id') == 'ratchet')
    pushing = [step for step in steps if 'inputs.push-key }}' in str(step.get('env'))]
    assert [step['if'] for step in pushing] == ["steps.ratchet.outputs.changed == 'true'"]
    assert steps.index(measuring) < steps.index(pushing[0])
    assert measuring['env']['COMMENT_RATCHET_PUSH_KEY_SET'] == "${{ inputs.push-key != '' }}"
    leaked = [step for step in steps if step not in pushing
              and 'inputs.push-key }}' in str(step)]
    assert not leaked


def test_a_default_branch_push_tightens_and_pushes(consumer):
    repo, origin, sha = consumer
    # A dirty working tree must not reach the records commit.
    (repo / 'notes.txt').write_text('edited\n')
    measured, pushed = run_action(repo, sha)
    assert measured.returncode == 0, measured.stderr
    assert pushed is not None and pushed.returncode == 0, pushed and pushed.stderr
    new = tip(origin)
    assert git(origin, 'rev-parse', f'{new}^') == sha
    assert git(origin, 'diff', '--name-only', sha, new) == RECORDS
    assert json.loads(git(origin, 'show', f'{new}:{RECORDS}')) == {'x.py': [3, 6]}
    assert git(origin, 'log', '-1', '--format=%an <%ae>|%cn <%ce>|%B', new).strip() == (
        'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>|'
        'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>|'
        'ci: tighten comment-ratchet records')
    # The key file never outlives the push step.
    assert not list((repo.parent / 'runner').iterdir())
    import push_records
    [call] = ssh_calls(repo)
    assert call['host'] == 'git@github.com' and call['path'] == 'owner/consumer.git'
    assert call['key'] == 'test-key\n' and call['mode'] == 0o600
    assert not call['key_in_env']
    assert call['hosts'] == ''.join(f'github.com {host}\n' for host in push_records.HOST_KEYS)
    assert {'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes'} <= set(call['options'])
    # The records commit's own run finds nothing to tighten and pushes nothing.
    git(repo, 'fetch', '-q', str(origin), 'main')
    git(repo, 'checkout', '-q', '-f', new)
    again, pushed = run_action(repo, new)
    assert again.returncode == 0, again.stderr
    assert pushed is None
    assert tip(origin) == new


def test_a_moved_branch_passes_without_pushing(consumer):
    repo, origin, sha = consumer
    (repo / 'y.py').write_text('y = 1\n')
    newer = commit(repo, 'a newer push')
    git(repo, 'push', '-q', str(origin), f'{newer}:refs/heads/main')
    git(repo, 'checkout', '-q', sha)
    measured, pushed = run_action(repo, sha)
    assert measured.returncode == 0, measured.stderr
    assert pushed.returncode == 0, pushed.stderr
    assert f'::notice::main moved past {sha}' in pushed.stdout
    assert tip(origin) == newer


def test_a_refusal_without_movement_fails_naming_it(consumer):
    repo, origin, sha = consumer
    hook = origin / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\necho denied by the ruleset >&2\nexit 1\n')
    hook.chmod(0o755)
    measured, pushed = run_action(repo, sha)
    assert measured.returncode == 0, measured.stderr
    assert pushed.returncode == 1
    assert f'refused while main stood at {sha}' in pushed.stderr
    assert 'denied by the ruleset' in pushed.stderr
    assert tip(origin) == sha
    assert not list((repo.parent / 'runner').iterdir())


def test_a_checkout_off_github_sha_fails_before_tightening(consumer):
    repo, origin, sha = consumer
    before = (repo / RECORDS).read_bytes()
    (repo / 'y.py').write_text('y = 1\n')
    commit(repo, 'checkout moved off the pushed commit')
    measured, pushed = run_action(repo, sha)
    assert measured.returncode == 1
    assert 'is not github.sha' in measured.stderr
    assert pushed is None
    assert (repo / RECORDS).read_bytes() == before
    assert tip(origin) == sha


@pytest.mark.parametrize('require_tight,want', [('false', 0), ('true', 1)])
def test_an_empty_key_names_each_loose_entry(consumer, require_tight, want):
    repo, origin, sha = consumer
    measured, pushed = run_action(repo, sha, key='', COMMENT_RATCHET_REQUIRE_TIGHT=require_tight)
    assert measured.returncode == want, measured.stderr
    assert pushed is None
    report = '~ x.py: required [3, 6], recorded [4, 6] (input push-key is unset'
    assert report in measured.stderr
    assert (f'::warning::{report}' in measured.stdout) is (require_tight == 'false')
    assert tip(origin) == sha


def test_a_pull_request_never_pushes(consumer):
    repo, origin, sha = consumer
    measured, pushed = run_action(repo, sha, GITHUB_EVENT_NAME='pull_request',
                                  GITHUB_REF='refs/pull/1/merge', COMMENT_RATCHET_BASE_REF=sha)
    assert measured.returncode == 0, measured.stderr
    assert pushed is None
    assert tip(origin) == sha


def test_a_malformed_records_file_fails(consumer):
    repo, origin, _ = consumer
    (repo / RECORDS).write_text('not json\n')
    sha = commit(repo, 'break the records')
    measured, pushed = run_action(repo, sha)
    assert measured.returncode == 1
    assert 'invalid records' in measured.stderr
    assert pushed is None


def test_an_ambient_ssh_command_cannot_replace_the_pinned_one(consumer):
    repo, origin, sha = consumer
    measured, pushed = run_action(repo, sha, GIT_SSH_COMMAND='false')
    assert pushed is not None and pushed.returncode == 0, pushed and pushed.stderr
    assert len(ssh_calls(repo)) == 1
    assert tip(origin) != sha


def test_an_unreadable_branch_after_a_refusal_fails(consumer):
    repo, origin, sha = consumer
    hook = origin / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    measured, pushed = run_action(repo, sha, FAKE_SSH_REFUSE='git-upload-pack')
    assert measured.returncode == 0, measured.stderr
    assert pushed.returncode == 1
    assert 'reading main failed' in pushed.stderr
    assert '::notice::' not in pushed.stdout
    assert tip(origin) == sha


def test_a_key_without_a_step_output_fails(consumer):
    repo, origin, sha = consumer
    measured, pushed = run_action(repo, sha, GITHUB_OUTPUT='')
    assert measured.returncode == 1
    assert 'GITHUB_OUTPUT is unset' in measured.stderr
    assert pushed is None and tip(origin) == sha
