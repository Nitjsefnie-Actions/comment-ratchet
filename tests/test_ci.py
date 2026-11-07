from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGS = ('suites', 'shellcheck', 'comment-share', 'codeql', 'secrets', 'audit')


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


def condition(expression, *, successful=True, cancelled=False, **contexts):
    import re
    from types import SimpleNamespace
    def namespace(value):
        return SimpleNamespace(**{key: namespace(item) for key, item in value.items()}) if isinstance(value, dict) else value
    source = expression.strip().removeprefix('${{').removesuffix('}}').strip()
    source = source.replace('&&', ' and ').replace('||', ' or ')
    source = re.sub(r'!(?!=)', ' not ', source)
    return bool(eval(source, {'__builtins__': {}, 'success': lambda: successful,
                             'cancelled': lambda: cancelled,
                             'always': lambda: True},
                     {key: namespace(value) for key, value in contexts.items()}))


def shell(script, directory, **env):
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=directory,
                          env=dict(os.environ, **{key: str(value) for key, value in env.items()}),
                          capture_output=True, text=True)


@pytest.mark.parametrize('event,result,cancelled,want', [
    ('pull_request', 'success', False, True),
    ('push', 'success', False, False),
    ('workflow_dispatch', 'success', False, False),
    ('pull_request', 'failure', False, False),
    ('pull_request', 'cancelled', False, False),
    ('pull_request', 'skipped', False, False),
    ('pull_request', 'success', True, False),
])
def test_coverage_artifact_only_from_measured_green_pr(event, result, cancelled, want):
    config = workflow('tests.yml')
    job = config['jobs']['diff-coverage']
    assert job['needs'] == 'suites'
    assert condition(job['if'], cancelled=cancelled,
                     needs={'suites': {'result': result}},
                     github={'event_name': event}) is want
    assert job['permissions'] == {'contents': 'read'}
    upload, = (step for step in job['steps']
               if 'name' in step.get('with', {}) and 'path' in step['with'])
    # No step-level bypass can turn a failed report into a posted number.
    assert all('if' not in step for step in job['steps'])
    consumer = workflow('coverage-comment.yml')
    assert consumer['on']['workflow_run']['workflows'] == [config['name']]
    download = next(step for step in consumer['jobs']['comment']['steps'] if 'uses' in step)
    assert download['with']['name'] == upload['with']['name']


@pytest.mark.parametrize('os_name,want', [('ubuntu-latest', True), ('macos-latest', False)])
def test_coverage_xml_has_one_uploading_cell(os_name, want):
    jobs = workflow('tests.yml')['jobs']
    # Artifact names share one run-wide namespace; exactly one matrix cell
    # supplies XML and exactly one job supplies the rendered comment.
    xml, = (step for step in jobs['suites']['steps']
            if 'name' in step.get('with', {}) and 'path' in step['with'])
    assert condition(xml['if'], matrix={'os': os_name}) is want
    writer = next(step for step in jobs['suites']['steps']
                  if step.get('name') == 'Write the coverage XML')
    assert condition(writer['if'], matrix={'os': os_name}) is want
    download, = (step for step in jobs['diff-coverage']['steps']
                 if 'name' in step.get('with', {}) and 'path' not in step['with'])
    assert download['with']['name'] == xml['with']['name']


def test_coverage_measures_isolated_action_subprocess(tmp_path):
    from tests.test_ci_comment_baseline import init_repo, commit
    repo = tmp_path / 'consumer'
    repo.mkdir()
    init_repo(repo)
    (repo / 'x.py').write_text('# hi\nx=1\n')
    commit(repo, 'source')
    driver = tmp_path / 'driver.py'
    driver.write_text('import os, subprocess, sys\n'
                      f'env=dict(os.environ, GITHUB_WORKSPACE={str(repo)!r}, COMMENT_RATCHET_MODE="tighten")\n'
                      f'subprocess.run([sys.executable,"-I",{str(ROOT / "run.py")!r}],env=env,check=True)\n')
    config = ROOT / '.coveragerc'
    env = dict(os.environ, COMMENT_RATCHET_COVERAGE_SOURCE_ROOT=str(ROOT),
               COVERAGE_FILE=str(tmp_path / '.coverage'))
    commands = [
        [sys.executable, '-m', 'coverage', 'run', '--rcfile', str(config), str(driver)],
        [sys.executable, '-m', 'coverage', 'combine', '--rcfile', str(config)],
        [sys.executable, '-m', 'coverage', 'json', '--rcfile', str(config), '-o', str(tmp_path / 'coverage.json')],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout+result.stderr
    files = json.loads((tmp_path / 'coverage.json').read_text())['files']
    for name in ('run.py', 'scripts/ci/comment_baseline.py', 'scripts/ci/comment_share.py'):
        assert files[name]['executed_lines'], name


def comment_step(name):
    return next(step['run'] for step in workflow('coverage-comment.yml')['jobs']['comment']['steps']
                if step['name'] == name)


@pytest.mark.parametrize('state', ['post', 'update', 'stale', 'wrong-pr', 'missing'])
def test_coverage_comment_posts_only_to_current_event_pr(tmp_path, state):
    (tmp_path / 'body.md').write_text('Python coverage: 80%. $(touch executed)\n')
    (tmp_path / 'pr-number.txt').write_text('2' if state == 'wrong-pr' else '1')
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = binary / 'gh'
    stub.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
args=sys.argv[1:]
endpoint=next((arg for arg in args if arg.startswith('repos/')), '')
if '-X' in args:
    record={{'endpoint':endpoint,'method':args[args.index('-X')+1],
             'body':pathlib.Path('comment.md').read_text()}}
    with open('writes.jsonl','a') as out: out.write(json.dumps(record)+'\\n')
elif endpoint.endswith('/comments'):
    if os.environ['COMMENT_STATE'] in ('update','missing'):
        print(json.dumps({{'id':7,'user':{{'login':'github-actions[bot]'}},
                           'body':'<!-- comment-ratchet-python-coverage -->\\nold coverage'}}))
elif endpoint.endswith('/pulls/1'):
    print('old-head' if os.environ['COMMENT_STATE']=='stale' else 'current-head')
else:
    sys.exit('unexpected gh request: '+repr(args))
''')
    stub.chmod(0o755)
    name = 'Mark missing Python coverage' if state == 'missing' else 'Post or update the pull request comment'
    result = shell(comment_step(name), tmp_path, GH_TOKEN='test-token', COMMENT_RATCHET_REPO='test/repo',
                   COMMENT_RATCHET_PR_NUMBER='1', COMMENT_RATCHET_HEAD_SHA='current-head',
                   COMMENT_STATE=state,
                   PATH=str(binary)+os.pathsep+os.environ['PATH'])
    assert result.returncode == (1 if state == 'wrong-pr' else 0), result.stderr
    writes = tmp_path / 'writes.jsonl'
    if state in ('stale','wrong-pr'):
        assert not writes.exists()
    else:
        sent = json.loads(writes.read_text())
        assert sent['endpoint'] == ('repos/test/repo/issues/1/comments' if state == 'post'
                                     else 'repos/test/repo/issues/comments/7')
        assert sent['method'] == ('POST' if state == 'post' else 'PATCH')
        assert 'current-head' in sent['body']
        if state == 'missing':
            assert 'not measured' in sent['body']
        else:
            assert 'Python coverage: 80%.' in sent['body']
    assert not (tmp_path / 'executed').exists()


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
    from tests.test_install_block import context, evaluate
    tests = workflow('tests.yml')
    callees = [job['uses'].removeprefix('./.github/workflows/').split(' ')[0]
               for job in tests['jobs'].values() if 'uses' in job]
    assert sorted(callees) == ['audit.yml', 'codeql.yml', 'secrets.yml']

    def groups(event, sha, run_id, **fields):
        import re
        github = {**context(event, sha=sha, workflow=tests['name'], **fields), 'github.run_id': run_id}
        return [re.sub(r'\$\{\{(.*?)\}\}', lambda match: str(evaluate(match[1], github)),
                       config['concurrency']['group'])
                for config in [tests] + [workflow(name) for name in callees]]
    pushed, records = groups('push', 'a1', '1'), groups('push', 'b2', '2')
    # One run's groups are distinct, and none is shared with the next run.
    assert len(set(pushed + records)) == 2 * len(pushed), (pushed, records)
    # A pull request's newer run still cancels its older run, callees included.
    older, newer = groups('pull_request', 'a1', '1', ref_name='1/merge'), groups(
        'pull_request', 'b2', '2', ref_name='1/merge')
    assert older[0] == newer[0]
    assert tests['concurrency']['cancel-in-progress'] == 'true'
