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
    ((20, 80), {'x.py': [20, 100]}, False),
    ((19, 20), {'x.py': [20, 100]}, False),
    ((21, 100), {'x.py': [20, 100]}, True),
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


@pytest.mark.parametrize('counts,want', [
    ((30, 100), [30, 100]),
    ((20, 50), [20, 50]),
    ((40, 200), [40, 100]),
    ((39, 50), [40, 100]),
    ((41, 200), [40, 100]),
    ((41, 100), [40, 100]),
    ((40, 80), [40, 100]),
    ((15, 100), None),
    ((40, 267), None),
])
def test_tighten_requires_fewer_comments_without_a_share_rise(counts, want):
    expected = {} if want is None else {'x.py': want}
    assert policy().tightened({'x.py': [40, 100]}, {'x.py': counts}) == expected


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


@pytest.mark.parametrize('code', ['x' * 62, 'x' * 58, 'y' * 60],
                         ids=['growth', 'shrink', 'replacement'])
def test_code_only_edits_leave_records_byte_identical(repo, code):
    source = repo / 'x.py'
    # # + 39 c = 40 comment bytes; 60 x = 60 code bytes: (40, 100).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    records = write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    before = records.read_bytes()
    # Comments stay 40; code grows to 62, shrinks to 58, or is replaced at 60.
    source.write_text('#' + 'c' * 39 + '\n' + code + '\n')
    commit(repo, 'edit only code')
    result = cli(repo, '--mode', 'tighten')
    assert result.returncode == 0, result.stderr
    assert records.read_bytes() == before


@pytest.mark.parametrize('code_bytes', [680, 681])
def test_code_only_graduation_drops_record(repo, code_bytes):
    # # + 119 c = 120 comments; 180 x = 180 code bytes: (120, 300).
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 119 + '\n' + 'x' * 180 + '\n')
    records = write_records(repo, {'x.py': [120, 300]})
    commit(repo, 'record policy')
    # 120 / (120 + 680) = 15%; one more code byte falls below target.
    source.write_text('#' + 'c' * 119 + '\n' + 'x' * code_bytes + '\n')
    commit(repo, 'add code to graduate')
    result = cli(repo, '--mode', 'tighten')
    assert result.returncode == 0, result.stderr
    assert json.loads(records.read_bytes()) == {}


@pytest.mark.parametrize('comments,code,want', [(45, 175, 1), (40, 170, 0),
                                             (41, 164, 0), (39, 150, 0)])
def test_default_branch_code_growth_sets_pr_limit(repo, comments, code, want):
    source = repo / 'x.py'
    # 40 comments (# + 39 c) + 60 code (x) = (40, 100).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    records = write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    before = records.read_bytes()
    # Same 40 comments + 160 code = (40, 200), without a records commit.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    base = commit(repo, 'default branch adds code')
    tightened = cli(repo, '--mode', 'tighten')
    assert tightened.returncode == 0, tightened.stderr
    assert records.read_bytes() == before
    # Heads: (45, 220), (40, 210), (41, 205), (39, 189).
    source.write_text('#' + 'c' * (comments - 1) + '\n' + 'x' * code + '\n')
    commit(repo, 'pr head')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == want, result.stderr
    if want:
        assert 'x.py: (45, 220) exceeds [40, 200]' in result.stderr


def test_equal_base_share_uses_base_comment_bytes(repo):
    # # + 79 c = 80 comments; 120 x = 120 code: (80, 200), equal to 40/100.
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 79 + '\n' + 'x' * 120 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'equal share base')
    # Same comments, 110 code: (80, 190), above the share but not comment limit.
    source.write_text('#' + 'c' * 79 + '\n' + 'x' * 110 + '\n')
    commit(repo, 'pr removes code')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 0, result.stderr


def test_only_changed_files_are_measured_at_base(repo, monkeypatch, capsys):
    name = 'nested/naïve name.py'
    source = repo / name
    source.parent.mkdir()
    # 40 comments (# + 39 c) + 160 code = (40, 200), below record 40/100.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    write_records(repo, {'x.py': [3, 6], name: [40, 100]})
    base = commit(repo, 'base with unchanged sibling')
    # 45 comments (# + 44 c) + 175 code = (45, 220), above base 40/200.
    source.write_text('#' + 'c' * 44 + '\n' + 'x' * 175 + '\n')
    commit(repo, 'pr changes one file')
    measure = policy().comment_share.measure_files
    base_paths = set()

    def observe(root, sources=None):
        if sources is not None:
            base_paths.update(sources)
        return measure(root, sources)

    monkeypatch.setattr(policy().comment_share, 'measure_files', observe)
    assert policy().run(repo, '.github/comment-share.json', 'check', 15, base) == 1
    assert f'{name}: (45, 220) exceeds [40, 200]' in capsys.readouterr().err
    assert base_paths == {name}


@pytest.mark.parametrize('comments,code,want', [(45, 60, 1), (40, 50, 0)])
def test_unmeasurable_base_source_falls_back_to_record(repo, comments, code, want):
    source = repo / 'x.py'
    source.write_text('def broken(\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'base source cannot be parsed')
    with pytest.raises(policy().comment_share.MeasurementError):
        policy().comment_share.measure_files(repo)
    # # + (comments - 1) c and code x: head pairs (45, 105) and (40, 90).
    source.write_text('#' + 'c' * (comments - 1) + '\n' + 'x' * code + '\n')
    commit(repo, 'pr repairs source')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == want, result.stderr
    assert 'cannot be parsed' not in result.stderr
    if want:
        assert 'x.py: (45, 105) exceeds [40, 100]' in result.stderr


def test_code_growth_then_deletion_accepts_current_base_share(repo):
    source = repo / 'x.py'
    # 40 comments (# + 39 c) + 60 code (x) = (40, 100).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    # Code grows to 160: (40, 200), then shrinks to 80: (40, 120).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    commit(repo, 'default branch adds code')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 80 + '\n')
    base = commit(repo, 'default branch removes some code')
    # 50 comments (# + 49 c) + 100 code = (50, 150), equal to base share.
    source.write_text('#' + 'c' * 49 + '\n' + 'x' * 100 + '\n')
    commit(repo, 'pr uses accepted loosening')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 0, result.stderr


def test_recorded_file_new_at_base_uses_record(repo):
    write_records(repo, {'x.py': [3, 6], 'new.py': [40, 100]})
    base = commit(repo, 'record absent file')
    # 41 comments (# + 40 c) + 59 code = (41, 100), above both record limbs.
    (repo / 'new.py').write_text('#' + 'c' * 40 + '\n' + 'x' * 59 + '\n')
    commit(repo, 'pr adds recorded file')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert 'new.py: (41, 100) exceeds [40, 100]' in result.stderr


def test_base_language_is_classified_from_base_content(repo):
    source = repo / 'tool'
    source.write_text('x' * 200 + '\n')
    write_records(repo, {'x.py': [3, 6], 'tool': [40, 100]})
    base = commit(repo, 'base extensionless file has no recognized language')
    # #!/bin/sh = 9, # + 30 c = 31, 60 x = 60: head pair (40, 100).
    source.write_text('#!/bin/sh\n#' + 'c' * 30 + '\n' + 'x' * 60 + '\n')
    commit(repo, 'pr gives file a shell shebang')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('source,want', [('# c\n', 1), ('x=1\n', 0)])
def test_empty_base_has_zero_share_limit(repo, source, want):
    (repo / 'x.py').write_text(' \t\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'empty base source')
    # Base has (0, 0); heads have (#c = 2) (2, 2) or (x=1 = 3) (0, 3).
    (repo / 'x.py').write_text(source)
    commit(repo, 'pr fills empty file')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == want, result.stderr
    if want:
        assert 'x.py: (2, 2) exceeds [0, 0]' in result.stderr


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


def test_code_deletion_cannot_loosen_records_across_prs(repo):
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 19 + '\n' + 'x' * 80 + '\n')
    records = write_records(repo, {'x.py': [20, 100]})
    base = commit(repo, 'base policy')
    assert policy().comment_share.measure_files(repo) == {'x.py': (20, 100)}

    source.write_text('#' + 'c' * 18 + '\nx\n')
    commit(repo, 'first pr removes code and one comment byte')
    assert policy().comment_share.measure_files(repo) == {'x.py': (19, 20)}
    first = cli(repo, '--base-ref', base)
    assert first.returncode == 0, first.stderr
    tightened = cli(repo, '--mode', 'tighten')
    assert tightened.returncode == 0, tightened.stderr
    assert json.loads(records.read_text()) == {'x.py': [20, 100]}
    next_base = git(repo, 'rev-parse', 'HEAD')

    source.write_text('#' + 'c' * 94 + '\nxxxxx\n')
    commit(repo, 'second pr adds comments')
    assert policy().comment_share.measure_files(repo) == {'x.py': (95, 100)}
    second = cli(repo, '--base-ref', next_base)
    assert second.returncode == 1, second.stdout
    assert 'x.py: (95, 100) exceeds [20, 100]' in second.stderr


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


@pytest.mark.parametrize('suffix,source,want', [
    ('ts', '// c\nexport const x = 1;\n', [3, 18]),
    ('tsx', '// c\nexport const x = 1;\n', [3, 18]),
    ('mts', '// c\nexport const x = 1;\n', [3, 18]),
    ('cts', '// c\nexport const x = 1;\n', [3, 18]),
    ('d.ts', '/** c */declare const x: number;\n', [6, 27]),
    ('sql', '-- c\nselect 1;\n', [3, 11]),
])
def test_typescript_sql_records_check_and_tighten(repo, suffix, source, want):
    name = f'new.{suffix}'
    (repo / name).write_text(source)
    write_records(repo, {'x.py': [3, 6]})
    base = commit(repo, 'add source with no record')
    failed = cli(repo, '--base-ref', base)
    assert failed.returncode == 1
    assert name in failed.stderr
    assert cli(repo, '--mode', 'tighten').returncode == 0
    records = json.loads((repo / '.github/comment-share.json').read_text())
    assert records[name] == want
    base = commit(repo, 'seed new language')
    assert cli(repo, '--base-ref', base).returncode == 0
    (repo / name).write_text(source + ('-- more\n' if suffix == 'sql' else '// more\n'))
    raised = cli(repo, '--base-ref', base)
    assert raised.returncode == 1
    assert name in raised.stderr
    assert cli(repo, '--mode', 'tighten').returncode == 0
    assert json.loads((repo / '.github/comment-share.json').read_text())[name] == want
    (repo / name).write_text('select 1;\n' if suffix == 'sql' else 'export const x=1;\n')
    assert cli(repo, '--base-ref', base).returncode == 0
    assert cli(repo, '--mode', 'tighten').returncode == 0
    assert name not in json.loads((repo / '.github/comment-share.json').read_text())
