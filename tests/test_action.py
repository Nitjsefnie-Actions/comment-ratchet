from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_ci_comment_baseline import commit, git, repo, write_records

ROOT = Path(__file__).resolve().parents[1]
# Clear run.py's prefixed and legacy input spellings so only metadata supplies them.
BARE = {name: None for name in (
    'COMMENT_RATCHET_TARGET', 'COMMENT_RATCHET_RECORDS', 'COMMENT_RATCHET_MODE',
    'COMMENT_RATCHET_BASE_REF', 'COMMENT_RATCHET_DEFAULT_BRANCH',
    'TARGET', 'RECORDS', 'MODE', 'BASE_REF', 'DEFAULT_BRANCH',
)}


def action(repo, **overrides):
    env = dict(os.environ, GITHUB_WORKSPACE=str(repo), GITHUB_EVENT_NAME='pull_request',
               COMMENT_RATCHET_DEFAULT_BRANCH='main', GITHUB_REF='refs/pull/1/merge',
               COMMENT_RATCHET_TARGET='15', COMMENT_RATCHET_RECORDS='.github/comment-share.json',
               COMMENT_RATCHET_MODE='auto',
               COMMENT_RATCHET_BASE_REF=git(repo, 'rev-parse', 'HEAD'))
    env.update(overrides)
    return subprocess.run([sys.executable, '-I', str(ROOT / 'run.py')], cwd=repo,
                          env={key: value for key, value in env.items() if value is not None},
                          capture_output=True, text=True, check=False)


def metadata_env(repo, inputs=None):
    """Env block of the action's run step, derived from action.yml alone.

    Optional inputs override the metadata defaults by input name, looked up
    through the metadata's own env mapping.
    """
    inputs = inputs or {}
    import yaml
    metadata = yaml.safe_load((ROOT / 'action.yml').read_text())
    contexts = {'github.event.pull_request.base.sha': git(repo, 'rev-parse', 'HEAD'),
                'github.event.repository.default_branch': 'main'}
    defaults = {name: contexts.get(str(item['default'])[4:-3], item['default'])
                for name, item in metadata['inputs'].items()}
    step = next(step for step in metadata['runs']['steps'] if 'run' in step)
    env = {}
    for env_key, expression in step['env'].items():
        ref = expression[4:-3]
        value = inputs.get(ref[7:]) if ref.startswith('inputs.') else contexts[ref]
        env[env_key] = str(defaults[ref[7:]] if value is None else value)
    return env


@pytest.mark.parametrize('name,value', [
    ('COMMENT_RATCHET_TARGET', '0'), ('COMMENT_RATCHET_TARGET', '-1'),
    ('COMMENT_RATCHET_TARGET', '15.5'), ('COMMENT_RATCHET_TARGET', 'abc'),
    ('COMMENT_RATCHET_TARGET', ''), ('COMMENT_RATCHET_TARGET', ' 15'),
    ('COMMENT_RATCHET_TARGET', '１５'),
    ('COMMENT_RATCHET_MODE', 'invalid'), ('COMMENT_RATCHET_MODE', ''),
    ('COMMENT_RATCHET_MODE', 'AUTO'),
    ('COMMENT_RATCHET_RECORDS', ''), ('COMMENT_RATCHET_RECORDS', '/tmp/policy.json'),
    ('COMMENT_RATCHET_RECORDS', '../policy.json'),
    ('COMMENT_RATCHET_RECORDS', 'a/../policy.json'), ('COMMENT_RATCHET_RECORDS', '.'),
    ('COMMENT_RATCHET_BASE_REF', ''), ('COMMENT_RATCHET_BASE_REF', '--upload-pack=evil'),
    ('COMMENT_RATCHET_BASE_REF', 'bad\nref'),
])
def test_invalid_input_names_the_input(repo, name, value):
    result = action(repo, **{name: value})
    assert result.returncode != 0
    assert name.removeprefix('COMMENT_RATCHET_').lower().replace('_', '-') in result.stderr
    assert not (repo / '.github/comment-share.json').exists()


@pytest.mark.parametrize('event,ref,default,mode,want', [
    ('pull_request', 'refs/pull/1/merge', 'main', 'auto', 0),
    ('push', 'refs/heads/main', 'main', 'auto', 0),
    ('push', 'refs/heads/master', 'master', 'auto', 0),
    ('push', 'refs/heads/topic', 'main', 'auto', 1),
    ('workflow_dispatch', 'refs/heads/main', 'main', 'auto', 1),
    ('pull_request_target', 'refs/heads/main', 'main', 'auto', 1),
    ('schedule', 'refs/heads/main', 'main', 'auto', 1),
    ('push', 'refs/tags/main', 'main', 'auto', 1),
    ('push', 'refs/heads/', '', 'auto', 1),
    ('workflow_dispatch', '', '', 'tighten', 0),
    ('workflow_dispatch', '', '', 'check', 0),
])
def test_mode_dispatch(repo, event, ref, default, mode, want):
    target = write_records(repo, {'x.py': [4, 6]})
    commit(repo, 'seed')
    before = target.read_bytes()
    result = action(repo, GITHUB_EVENT_NAME=event, GITHUB_REF=ref,
                    COMMENT_RATCHET_DEFAULT_BRANCH=default, COMMENT_RATCHET_MODE=mode)
    assert result.returncode == want, result.stderr
    tightened = mode == 'tighten' or (mode == 'auto' and event == 'push' and want == 0)
    if tightened:
        assert target.read_bytes() != before
        assert json.loads(target.read_bytes()) == {'x.py': [3, 6]}
    else:
        assert target.read_bytes() == before
    if want:
        assert 'mode' in result.stderr and event in result.stderr


def test_forced_tighten_seeds_and_ignores_missing_check_revision(repo):
    result = action(repo, COMMENT_RATCHET_MODE='tighten', COMMENT_RATCHET_BASE_REF='')
    assert result.returncode == 0, result.stderr
    assert json.loads((repo / '.github/comment-share.json').read_text()) == {'x.py': [3, 6]}


def test_input_text_never_executes(repo):
    result = action(repo, COMMENT_RATCHET_MODE='tighten', COMMENT_RATCHET_TARGET='$(touch owned)')
    assert result.returncode == 1
    assert 'target' in result.stderr
    assert not (repo / 'owned').exists()


def test_consumer_code_is_not_imported_or_executed(repo):
    marker = repo / 'executed'
    evil = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
    for name in ('yaml.py', 'comment_share.py', 'sitecustomize.py', 'usercustomize.py'):
        (repo / name).write_text(evil)
    (repo / 'package.json').write_text(json.dumps({'scripts': {'preinstall': 'touch executed'}}))
    (repo / 'a.yaml').write_text('# hi\na: 1\n')
    (repo / 'a.js').write_text('// hi\nconst x=1;\n')
    git(repo, 'add', '.')
    result = action(repo, COMMENT_RATCHET_MODE='tighten', PYTHONPATH=str(repo),
                    COMMENT_RATCHET_ESPREE_DIR=str(repo))
    assert result.returncode == 0, result.stderr
    records = json.loads((repo / '.github/comment-share.json').read_bytes())
    assert records['a.yaml'] == [3, 6]
    assert records['a.js'] == [4, 13]
    assert not marker.exists()


def test_records_symlink_cannot_write_outside_checkout(repo, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.json')
    outside.write_text('{}\n')
    (repo / 'policy.json').symlink_to(outside)
    result = action(repo, COMMENT_RATCHET_MODE='tighten', COMMENT_RATCHET_RECORDS='policy.json')
    assert result.returncode == 1 and 'records' in result.stderr
    assert outside.read_text() == '{}\n'


def test_custom_inputs_reach_policy(repo):
    result = action(repo, COMMENT_RATCHET_MODE='tighten', COMMENT_RATCHET_TARGET='50',
                    COMMENT_RATCHET_RECORDS='custom.json')
    assert result.returncode == 0, result.stderr
    assert json.loads((repo / 'custom.json').read_bytes()) == {}
    assert not (repo / '.github/comment-share.json').exists()


def test_bare_uses_defaults_from_metadata(repo):
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'exact seed')
    result = action(repo, **{**BARE, **metadata_env(repo)})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('inputs', [
    {'target': '0'},
    {'mode': 'tighten', 'records': 'custom.json'},
])
def test_metadata_env_mapping_drives_the_action(repo, inputs):
    target = write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'exact seed')
    before = target.read_bytes()
    result = action(repo, **{**BARE, **metadata_env(repo, inputs)})
    if 'target' in inputs:
        assert result.returncode == 1, result.stderr
        assert 'target' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((repo / 'custom.json').read_bytes()) == {'x.py': [3, 6]}
        assert target.read_bytes() == before


def test_typescript_uses_action_dependency_from_consumer_checkout(repo):
    marker = repo / 'executed'
    package = repo / 'node_modules' / 'typescript'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('{"main":"index.js"}')
    (package / 'index.js').write_text(
        f'require("node:fs").writeFileSync({str(marker)!r}, "executed"); throw Error("consumer");')
    (repo / 'a.ts').write_text('// c\nexport const x = 1;\n')
    git(repo, 'add', 'a.ts')
    result = action(repo, COMMENT_RATCHET_MODE='tighten', NODE_PATH=str(repo / 'node_modules'))
    assert result.returncode == 0, result.stderr
    assert json.loads((repo / '.github/comment-share.json').read_text())['a.ts'] == [3, 18]
    assert not marker.exists()


@pytest.mark.parametrize('name,good,bad,message', [
    ('TARGET', '15', '0', 'target'),
    ('RECORDS', '.github/comment-share.json', '../policy.json', 'records'),
    ('MODE', 'auto', 'invalid', 'mode'),
    ('DEFAULT_BRANCH', 'main', 'other', 'mode'),
    ('BASE_REF', 'HEAD', '--upload-pack=evil', 'base-ref'),
])
@pytest.mark.parametrize('case,want', [
    ('legacy', 0), ('legacy-invalid', 1), ('prefixed', 0),
    ('prefixed-invalid', 1), ('prefixed-empty', 1),
])
def test_environment_fallback_and_precedence(repo, name, good, bad, message, case, want):
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'exact seed')
    legacy, prefixed = {
        'legacy': (good, None), 'legacy-invalid': (bad, None),
        'prefixed': (bad, good), 'prefixed-invalid': (good, bad),
        'prefixed-empty': (good, ''),
    }[case]
    overrides = {name: legacy, 'COMMENT_RATCHET_' + name: prefixed}
    if name == 'DEFAULT_BRANCH':
        overrides.update(GITHUB_EVENT_NAME='push', GITHUB_REF='refs/heads/main')
    result = action(repo, **overrides)
    assert result.returncode == want, result.stderr
    if want:
        assert message in result.stderr
