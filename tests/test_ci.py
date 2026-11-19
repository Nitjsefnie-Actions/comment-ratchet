from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGS = ('plan', 'test', 'report', 'shellcheck', 'comment-share', 'codeql', 'secrets', 'audit')


def fold(results):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/ci/aggregate_gate.py')],
                          env=dict(os.environ, COMMENT_RATCHET_NEEDS_JSON=json.dumps(results)),
                          capture_output=True, text=True, check=False)


def test_aggregate_all_success():
    result = fold({name: {'result': 'success'} for name in LEGS})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('leg', LEGS)
@pytest.mark.parametrize('state', ['failure', 'cancelled', 'skipped', 'missing'])
def test_aggregate_fails_closed(leg, state):
    results = {name: {'result': 'success'} for name in LEGS}
    if state == 'missing':
        del results[leg]
    else:
        results[leg]['result'] = state
    result = fold(results)
    assert result.returncode == 1
    assert leg in result.stderr


@pytest.mark.parametrize('payload', ['null', '[]', '{}', 'not json'])
def test_aggregate_bad_payload_fails(payload):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/aggregate_gate.py')],
                            env=dict(os.environ, COMMENT_RATCHET_NEEDS_JSON=payload), capture_output=True,
                            text=True, check=False)
    assert result.returncode == 1


def workflow(name):
    from ruamel.yaml import YAML
    return YAML(typ='base', pure=True).load(ROOT / '.github/workflows' / name)


def shell(script, directory, **env):
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=directory,
                          env=dict(os.environ, **{key: str(value) for key, value in env.items()}),
                          capture_output=True, text=True)


def test_actionlint_is_a_standalone_required_check():
    lint = workflow('actionlint.yml')
    triggers = lint['on']
    assert 'pull_request' in triggers
    assert not triggers['pull_request']
    assert triggers['push']['branches'] == ['main']
    assert 'workflow_dispatch' in triggers
    assert 'workflow_call' not in triggers
    job = lint['jobs']['actionlint']
    assert job.get('name', 'actionlint') == 'actionlint'
    assert 'runs-on' in job and 'uses' not in job
    tests = workflow('tests.yml')['jobs']
    for caller in tests.values():
        assert Path(caller.get('uses', '').split('@', 1)[0]).name != 'actionlint.yml'
    assert set(tests['aggregate']['needs']) == set(LEGS)


@pytest.mark.parametrize('case,want', [
    ('legacy', 0), ('legacy-invalid', 1), ('prefixed', 0),
    ('prefixed-invalid', 1), ('prefixed-empty', 1), ('unset', 1),
])
def test_aggregate_environment_fallback_and_precedence(monkeypatch, case, want):
    good = json.dumps({name: {'result': 'success'} for name in LEGS})
    bad = 'not json'
    legacy, prefixed = {
        'legacy': (good, None), 'legacy-invalid': (bad, None),
        'prefixed': (bad, good), 'prefixed-invalid': (good, bad),
        'prefixed-empty': (good, ''), 'unset': (None, None),
    }[case]
    monkeypatch.delenv('NEEDS_JSON', raising=False)
    monkeypatch.delenv('COMMENT_RATCHET_NEEDS_JSON', raising=False)
    env = dict(os.environ)
    if legacy is not None:
        env['NEEDS_JSON'] = legacy
    if prefixed is not None:
        env['COMMENT_RATCHET_NEEDS_JSON'] = prefixed
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/aggregate_gate.py')],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == want, result.stderr
    if want:
        assert 'CI aggregate failed:' in result.stderr
    if case == 'unset':
        assert "'NEEDS_JSON'" in result.stderr


@pytest.mark.parametrize('event,ref_type,ref_name,environment,mode,require_tight,key', [
    ('pull_request', 'branch', '1/merge', '', 'check', 'false', ''),
    ('push', 'branch', 'main', 'main-push', 'tighten', 'true', 'deploy-key'),
    ('push', 'branch', 'other', '', 'tighten', 'true', 'deploy-key'),
    ('push', 'tag', 'main', '', 'tighten', 'true', 'deploy-key'),
    ('workflow_dispatch', 'branch', 'main', '', 'check', 'true', ''),
])
def test_comment_share_commits_its_records_only_on_a_main_push(
        event, ref_type, ref_name, environment, mode, require_tight, key):
    from tests.test_install_block import context, evaluate, install_block
    job = workflow('tests.yml')['jobs']['comment-share']
    # A workflow_call callee would read the environment secret empty.
    assert 'runs-on' in job and 'uses' not in job
    assert job['environment'] == install_block()[1]['jobs']['comment-share']['environment']
    base = 'b0' if event == 'pull_request' else ''
    github = {**context(event, ref_type=ref_type, ref_name=ref_name), 'github.event.pull_request.base.sha': base,
              'secrets.MASTER_PUSH_DEPLOY_KEY': 'deploy-key'}
    assert evaluate(job['environment'], github) == environment
    _, guard, action = job['steps']
    assert bool(evaluate(guard['if'], github)) is (environment == 'main-push')
    for secret in ('deploy-key', ''):
        assert evaluate(guard['env']['COMMENT_RATCHET_PUSH_KEY_SET'],
                        {**github, 'secrets.MASTER_PUSH_DEPLOY_KEY': secret}) is bool(secret)
    assert action['uses'] == './'
    assert {name: evaluate(value, github) for name, value in action['with'].items()} == {
        'mode': mode, 'base-ref': base or 'a1', 'require-tight': require_tight, 'push-key': key}


@pytest.mark.parametrize('key_set,want', [('true', 0), ('false', 1), ('', 1)])
def test_a_keyless_main_push_fails_before_the_action_runs(tmp_path, key_set, want):
    guard = workflow('tests.yml')['jobs']['comment-share']['steps'][1]
    result = shell(guard['run'], tmp_path, COMMENT_RATCHET_PUSH_KEY_SET=key_set)
    assert result.returncode == want, result.stdout + result.stderr
    assert ('MASTER_PUSH_DEPLOY_KEY reads empty' in result.stdout) is bool(want)


def test_a_main_push_run_cancels_no_job_of_an_earlier_main_push_run():
    import re
    from tests.test_install_block import context, evaluate
    tests = workflow('tests.yml')
    callees = [workflow(job['uses'].removeprefix('./.github/workflows/'))
               for job in tests['jobs'].values() if 'uses' in job]
    assert callees

    def group(config, event, sha, run_id, workflow=tests['name'], **fields):
        github = {**context(event, sha=sha, workflow=workflow, **fields), 'github.run_id': run_id}
        return re.sub(r'\$\{\{(.*?)\}\}', lambda match: str(evaluate(match[1], github)),
                      config['concurrency']['group'])

    def groups(*run, **fields):
        return [group(config, *run, **fields) for config in [tests] + callees]
    pushed, records = groups('push', 'a1', '1'), groups('push', 'b2', '2')
    # One run's groups are distinct, and none is shared with the next run.
    assert len(set(pushed + records)) == 2 * len(pushed), (pushed, records)
    # A pull request's newer run still cancels its older run, callees included.
    older, newer = groups('pull_request', 'a1', '1', ref_name='1/merge'), groups(
        'pull_request', 'b2', '2', ref_name='1/merge')
    assert older[0] == newer[0]
    assert tests['concurrency']['cancel-in-progress'] == 'true'
    # A standalone run keeps its per-ref group, so a newer one on a branch cancels the older.
    for config in callees:
        name = config['name']
        assert group(config, 'push', 'a1', '1', workflow=name, ref_name='feature') == \
            group(config, 'push', 'b2', '2', workflow=name, ref_name='feature') == f'{name}-refs/heads/feature'
