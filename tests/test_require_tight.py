"""The require-tight records map over changed paths.

Head records must equal what tighten writes from the base records: unchanged
files carry their pair byte-for-byte, changed files carry the tightening
(lowered, dropped, or added), and every differing entry is named.
"""
from __future__ import annotations

import pytest

from tests.test_ci_comment_baseline import cli, commit, git, repo, write_records


@pytest.mark.parametrize('comments,code,want', [(45, 175, 1), (40, 170, 0),
                                             (41, 164, 0)])
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
    # Heads: (45, 220), (40, 210), (41, 205).
    source.write_text('#' + 'c' * (comments - 1) + '\n' + 'x' * code + '\n')
    commit(repo, 'pr head')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == want, result.stderr
    if want:
        assert 'x.py: (45, 220) exceeds [40, 200]' in result.stderr


def test_head_below_recorded_comments_carries_the_lowered_record(repo):
    source = repo / 'x.py'
    # 40 comments (# + 39 c) + 60 code (x) = (40, 100); the default branch
    # then adds code to (40, 200) without a records commit.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    base = commit(repo, 'default branch adds code')
    # One comment byte fewer at a lower share: (39, 189) lowers the record.
    source.write_text('#' + 'c' * 38 + '\n' + 'x' * 150 + '\n')
    commit(repo, 'pr removes one comment byte')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '~ x.py: required [39, 189], recorded [40, 100]' in result.stderr
    write_records(repo, {'x.py': [39, 189]})
    commit(repo, 'carry the lowered record')
    assert cli(repo, '--base-ref', base).returncode == 0

@pytest.mark.parametrize('base_comments,base_code,head_comments,head_code', [
    (10, 90, 12, 88),
    (0, 0, 1, 9),
    (10, 90, 15, 85),
], ids=['below-target', 'empty-base', 'at-target'])
def test_graduated_head_carries_the_dropped_entry(repo, base_comments, base_code,
                                                  head_comments, head_code):
    source = repo / 'x.py'
    # Bases: (10, 100) or (0, 0); record 40/100 is above target 15%.
    source.write_text(('#' + 'c' * (base_comments - 1) if base_comments else '')
                      + '\n' + 'x' * base_code + '\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'base graduated before records update')
    # Heads: (12, 100), (1, 10), (15, 100), all at or below target: tighten
    # drops the entry, so the pull request carries the drop.
    source.write_text('#' + 'c' * (head_comments - 1) + '\n' + 'x' * head_code + '\n')
    commit(repo, 'pr head stays graduated')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '- x.py: recorded [40, 100]' in result.stderr
    write_records(repo, {})
    commit(repo, 'carry the graduation')
    assert cli(repo, '--base-ref', base).returncode == 0

@pytest.mark.parametrize('source,line', [('# c\n', 'x.py: (2, 2) exceeds [0, 0]'),
                                         ('x=1\n', '- x.py: recorded [40, 100]')])
def test_empty_base_has_zero_share_limit(repo, source, line):
    (repo / 'x.py').write_text(' \t\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'empty base source')
    # Base has (0, 0); heads have (#c = 2) (2, 2) or (x=1 = 3) (0, 3): the
    # first exceeds the effective limit, the second carries the graduation.
    (repo / 'x.py').write_text(source)
    commit(repo, 'pr fills empty file')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1, result.stderr
    assert line in result.stderr


def test_graduated_head_below_target_requires_the_record_drop(repo):
    (repo / 'x.py').write_text(' \t\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'empty base source')
    # (0, 3) is at or under the target: the pull request carries the drop.
    (repo / 'x.py').write_text('x=1\n')
    commit(repo, 'pr fills empty file')
    write_records(repo, {})
    commit(repo, 'carry the graduation')
    assert cli(repo, '--base-ref', base).returncode == 0


def test_existing_base_records_bind_even_when_head_is_looser(repo):
    write_records(repo, {'x.py': [2, 6]})
    base = commit(repo, 'base policy')
    write_records(repo, {'x.py': [6, 6]})
    commit(repo, 'looser head policy')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '~ x.py: required [2, 6], recorded [6, 6]' in result.stderr


def test_head_records_deleted_or_invalid_cannot_dodge_the_required_map(repo):
    target = write_records(repo, {'x.py': [3, 6]})
    base = commit(repo, 'base policy')
    target.unlink()
    commit(repo, 'head records deleted')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '+ x.py: required [3, 6]' in result.stderr
    target.write_text('{bad json')
    commit(repo, 'head records invalid')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert 'records' in result.stderr


def test_head_records_absent_when_nothing_is_required_passes(repo):
    # The base holds one entry for a file this change deletes; tightening the
    # head writes an empty map, so an absent head records file matches it.
    target = write_records(repo, {'x.py': [3, 6]})
    (repo / 'y.py').write_text('# yes\n')
    commit(repo, 'add a recorded file to delete')
    write_records(repo, {'y.py': [4, 4]})
    base = commit(repo, 'base policy')
    target.unlink()
    git(repo, 'rm', '-q', 'y.py')
    commit(repo, 'pr deletes the only recorded file')
    assert cli(repo, '--base-ref', base).returncode == 0


def test_all_offending_files_are_named_with_numbers(repo):
    write_records(repo, {'x.py': [2, 6]})
    base = commit(repo, 'base policy')
    (repo / 'new.sh').write_text('# yes\n')
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'add a file and loosen the carried entry')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '~ x.py: required [2, 6], recorded [3, 6]' in result.stderr
    assert '+ new.sh: required [4, 4]' in result.stderr
