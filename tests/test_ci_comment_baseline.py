from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def policy():
    import comment_baseline
    return comment_baseline


@pytest.mark.parametrize('counts,baseline,want', [
    ((15, 100), {}, False),
    ((149999999999999999, 1000000000000000000), {}, False),
    ((150000000000000001, 1000000000000000000), {}, True),
    ((20, 100), {'x.py': [19, 100]}, True),
    ((25, 100), {'x.py': [30, 100]}, False),
    ((35, 200), {'x.py': [20, 100]}, False),
    ((15, 100), {'x.py': [20, 100]}, False),
    ((40, 200), {'x.py': [20, 100]}, False),
    ((14, 100), {'x.py': [20, 100]}, False),
    ((0, 0), {}, False),
])
def test_exact_boundary_and_rise(counts, baseline, want):
    assert bool(policy().violations({'x.py': counts}, baseline)) is want


def test_tighten_lowers_drops_and_never_raises():
    baseline = {'lower.py': [30, 100], 'drop.py': [20, 100],
                'rise.py': [20, 100], 'gone.py': [20, 100]}
    measured = {'lower.py': (25, 100), 'drop.py': (15, 100), 'rise.py': (21, 100)}
    assert policy().tightened(baseline, measured) == {
        'lower.py': [25, 100], 'rise.py': [20, 100]}


@pytest.mark.parametrize('entry', [[101, 100], [20.5, 100], [True, 100],
                                   [20, 0], [-1, 100], [20], '20%', [20, 100, 1]])
def test_invalid_entry_refused(entry):
    with pytest.raises(ValueError, match='x.py'):
        policy().decode_records(json.dumps({'x.py': entry}))


@pytest.mark.parametrize('path', ['../x.py', '/x.py', 'a/../x.py', './x.py',
                                 'a//x.py', 'a\\x.py', '', 'a\nx.py'])
def test_record_paths_safe(path):
    with pytest.raises(ValueError):
        policy().decode_records(json.dumps({path: [20, 100]}))


@pytest.mark.parametrize('raw', ['[]', 'null', '{"x.py":[20,100],"x.py":[30,100]}'])
def test_records_are_an_unambiguous_object(raw):
    with pytest.raises(ValueError):
        policy().decode_records(raw)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def init_repo(root):
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Comment ratchet tests')
    git(root, 'config', 'user.email', 'tests@example.com')


def commit(root, subject):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', subject)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    init_repo(tmp_path)
    (tmp_path / 'x.py').write_text('# hi\nx = 1\n')
    # Literal fixture: #hi is 3 bytes, x=1 is 3, so x.py is [3, 6].
    commit(tmp_path, 'base without records')
    return tmp_path


def write_records(repo, data, name='.github/comment-share.json'):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def cli(repo, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/ci/comment_baseline.py'),
                           '--root', str(repo), *args],
                          capture_output=True, text=True, check=False)


def test_tighten_seeds_without_extra_step_and_serializes(repo):
    (repo / 'a.sh').write_text('# yes\n')
    (repo / 'empty.py').write_text(' \t\n')
    git(repo, 'add', '.')
    result = cli(repo, '--mode', 'tighten')
    assert result.returncode == 0, result.stderr
    target = repo / '.github/comment-share.json'
    assert target.read_text() == '{\n  "a.sh": [\n    4,\n    4\n  ],\n  "x.py": [\n    3,\n    6\n  ]\n}\n'
    first = target.read_bytes()
    assert cli(repo, '--mode', 'tighten').returncode == 0
    assert target.read_bytes() == first


@pytest.mark.parametrize('head,want,names', [
    ({'x.py': [3, 6]}, 0, []),
    ({'x.py': [4, 6]}, 1, ['x.py']),
    ({}, 1, ['x.py']),
    ({'x.py': [3, 6], 'extra.py': [2, 3]}, 1, ['extra.py']),
    (None, 1, ['tighten']),
    ({'x.py': [2, 6]}, 1, ['x.py']),
    ({'x.py': [6, 12]}, 1, ['x.py']),
    ({'extra.py': [2, 3]}, 1, ['x.py', 'extra.py']),
])
def test_absent_base_requires_exact_head_seed(repo, head, want, names):
    base = git(repo, 'rev-parse', 'HEAD')
    if head is not None:
        write_records(repo, head)
        commit(repo, 'seed')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == want, result.stderr
    for name in names:
        assert name in result.stderr
    if head is None:
        assert 'seed' in result.stderr


def test_existing_base_records_bind_even_when_head_is_looser(repo):
    write_records(repo, {'x.py': [2, 6]})
    base = commit(repo, 'base policy')
    write_records(repo, {'x.py': [6, 6]})
    commit(repo, 'looser head policy')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert 'x.py' in result.stderr
    assert '(3, 6)' in result.stderr and '[2, 6]' in result.stderr


def test_base_records_bind_even_when_head_is_deleted_or_invalid(repo):
    target = write_records(repo, {'x.py': [3, 6]})
    base = commit(repo, 'base policy')
    for body in (None, '{bad json'):
        if body is None:
            target.unlink()
        else:
            target.write_text(body)
        commit(repo, 'head policy changed')
        assert cli(repo, '--base-ref', base).returncode == 0


def test_all_offending_files_are_named_with_numbers(repo):
    write_records(repo, {'x.py': [2, 6]})
    base = commit(repo, 'base policy')
    (repo / 'new.sh').write_text('# yes\n')
    git(repo, 'add', 'new.sh')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert 'x.py: (3, 6) exceeds [2, 6]' in result.stderr
    assert 'new.sh: (4, 4) exceeds (15, 100)' in result.stderr


def test_lower_equal_deleted_and_graduated_pass_then_tighten(repo):
    write_records(repo, {'x.py': [4, 6], 'gone.sh': [2, 3]})
    base = commit(repo, 'base policy')
    assert cli(repo, '--base-ref', base).returncode == 0
    assert cli(repo, '--mode', 'tighten').returncode == 0
    assert json.loads((repo / '.github/comment-share.json').read_text()) == {'x.py': [3, 6]}
    assert cli(repo, '--base-ref', base).returncode == 0
    (repo / 'x.py').write_text('x=1\n')
    assert cli(repo, '--base-ref', base).returncode == 0
    assert cli(repo, '--mode', 'tighten').returncode == 0
    assert json.loads((repo / '.github/comment-share.json').read_text()) == {}


def test_custom_target_and_records(repo):
    result = cli(repo, '--mode', 'tighten', '--target', '50', '--records', 'policy.json')
    assert result.returncode == 0, result.stderr
    assert json.loads((repo / 'policy.json').read_text()) == {}
    base = commit(repo, 'at target')
    assert cli(repo, '--base-ref', base, '--target', '50', '--records', 'policy.json').returncode == 0


def test_invalid_base_does_not_fall_back(repo):
    write_records(repo, {'x.py': [3, 6]})
    result = cli(repo, '--base-ref', 'nonexistent')
    assert result.returncode == 1
    assert 'base-ref' in result.stderr


def test_missing_base_object_is_fetched_in_consumer_checkout(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'x.py').write_text('# hi\nx=1\n')
    write_records(origin, {'x.py': [3, 6]})
    base = commit(origin, 'base')
    head = tmp_path / 'consumer'
    git(tmp_path, 'clone', '-q', str(origin), str(head))
    (origin / 'x.py').write_text('# hi\nx=1\ny=2\n')
    remote_base = commit(origin, 'new remote base')
    assert git(head, 'rev-parse', 'HEAD') == base
    result = cli(head, '--base-ref', remote_base)
    assert result.returncode == 0, result.stderr
    assert git(head, 'rev-parse', 'FETCH_HEAD') == remote_base
    assert git(head, 'rev-parse', '--is-shallow-repository') == 'false'


def test_malformed_base_records_cannot_use_head_fallback(repo):
    target = write_records(repo, {'x.py': [3, 6]})
    target.write_text('not json')
    base = commit(repo, 'invalid base records')
    write_records(repo, {'x.py': [3, 6]})
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert 'records' in result.stderr


def test_tighten_adds_new_records_without_raising_existing(repo):
    write_records(repo, {'x.py': [2, 6], 'gone.py': [2, 3], 'graduated.py': [2, 3]})
    (repo / 'new.sh').write_text('# yes\n')
    (repo / 'graduated.py').write_text('x=1\n')
    git(repo, 'add', '.')
    result = cli(repo, '--mode', 'tighten')
    assert result.returncode == 0, result.stderr
    assert json.loads((repo / '.github/comment-share.json').read_bytes()) == {
        'x.py': [2, 6], 'new.sh': [4, 4]}
