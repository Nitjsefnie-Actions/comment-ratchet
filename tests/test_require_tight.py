"""The require-tight records map over changed paths."""
from __future__ import annotations

import pytest

from tests.test_ci_comment_baseline import cli, commit, git, repo, write_records


@pytest.mark.parametrize('comments,code,want', [(45, 175, 1), (40, 170, 0),
                                             (41, 164, 0)])
def test_default_branch_code_growth_sets_pr_limit(repo, comments, code, want):
    source = repo / 'x.py'
    # (40, 100).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    records = write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    before = records.read_bytes()
    # (40, 200), no records commit.
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
    # (40, 100), then the default branch adds code to (40, 200).
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    base = commit(repo, 'default branch adds code')
    # One byte fewer at a lower share: (39, 189) lowers the record.
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
    source.write_text(('#' + 'c' * (base_comments - 1) if base_comments else '')
                      + '\n' + 'x' * base_code + '\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'base graduated before records update')
    # Heads at or below target: tighten drops, the PR carries.
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
    # Base (0, 0); heads (2, 2) or (0, 3).
    (repo / 'x.py').write_text(source)
    commit(repo, 'pr fills empty file')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1, result.stderr
    assert line in result.stderr


def test_graduated_head_below_target_requires_the_record_drop(repo):
    (repo / 'x.py').write_text(' \t\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'empty base source')
    # (0, 3) is at or under target: carry the drop.
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


DELETED = '.github/comment-share.json: deleted by this pull request'


def test_deleting_the_records_file_fails_when_nothing_is_required(repo):
    # x.py (3, 6) is an untouched unrecorded skew: its entry is optional.
    target = write_records(repo, {'x.py': [3, 6]})
    (repo / 'y.py').write_text('# yes\n')
    commit(repo, 'add a recorded file to delete')
    write_records(repo, {'y.py': [4, 4]})
    base = commit(repo, 'base policy')
    target.unlink()
    git(repo, 'rm', '-q', 'y.py')
    commit(repo, 'pr deletes the only recorded file and the records')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert DELETED in result.stderr
    write_records(repo, {})
    commit(repo, 'keep an empty records file')
    kept = cli(repo, '--base-ref', base)
    assert kept.returncode == 0, kept.stderr
    assert DELETED not in kept.stderr


def test_trimming_every_recorded_file_cannot_delete_the_records(repo):
    names = ('x.py', 'new.py')
    for name in names:
        (repo / name).write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    target = write_records(repo, {name: [40, 100] for name in names})
    base = commit(repo, 'record policy')
    for name in names:
        (repo / name).write_text('#' + 'c' * 9 + '\n' + 'x' * 90 + '\n')
    target.unlink()
    commit(repo, 'pr trims both files and deletes the records')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert DELETED in result.stderr
    write_records(repo, {})
    commit(repo, 'carry both graduations in an empty records file')
    assert cli(repo, '--base-ref', base).returncode == 0


def test_a_branch_from_before_adoption_need_not_carry_the_records(repo):
    git(repo, 'branch', 'pr')
    write_records(repo, {'x.py': [3, 6]})
    base = commit(repo, 'adopt')
    git(repo, 'checkout', '-q', 'pr')
    (repo / 'y.py').write_text('y = 1\n')
    commit(repo, 'pr branched before adoption')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 0, result.stderr
    assert DELETED not in result.stderr


def test_all_offending_files_are_named_with_numbers(repo):
    write_records(repo, {'x.py': [2, 6]})
    base = commit(repo, 'base policy')
    (repo / 'new.sh').write_text('# yes\n')
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'add a file and loosen the carried entry')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '~ x.py: required [2, 6], recorded [3, 6]' in result.stderr
    # The unrecorded file is judged against the target, not admitted by
    # carrying its own record: the repair is trimming, never recording.
    assert 'new.sh: (4, 4) exceeds the target (15, 100)' in result.stderr
    assert 'never record it' in result.stderr
    assert '+ new.sh' not in result.stderr


def test_changed_unrecorded_above_target_fails_until_trimmed(repo):
    write_records(repo, {'x.py': [3, 6]})
    base = commit(repo, 'base policy')
    # A changed unrecorded file above the target fails naming the file and
    # its pair, exactly as in v1; the repair is trimming, never recording.
    (repo / 'new.sh').write_text('# yes\n')
    commit(repo, 'pr adds a file above the target')
    unrecorded = cli(repo, '--base-ref', base)
    assert unrecorded.returncode == 1
    assert 'new.sh: (4, 4) exceeds the target (15, 100)' in unrecorded.stderr
    assert 'never record it' in unrecorded.stderr
    # Recording its own pair in the same pull request does not admit it: the
    # require-tight map adds no entry for a changed unrecorded file.
    write_records(repo, {'x.py': [3, 6], 'new.sh': [4, 4]})
    commit(repo, 'pr records its own file instead')
    recorded = cli(repo, '--base-ref', base)
    assert recorded.returncode == 1
    assert 'new.sh: (4, 4) exceeds the target (15, 100)' in recorded.stderr
    assert '+ new.sh' not in recorded.stderr
    assert '- new.sh: recorded [4, 4]' in recorded.stderr
    # Trimming below the target and dropping the entry in the same pull
    # request passes.
    (repo / 'new.sh').write_text('x = 1\n')
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'pr trims the file below the target')
    assert cli(repo, '--base-ref', base).returncode == 0


def test_changed_recorded_file_dropping_its_entry_fails(repo):
    # A pull request that grows a recorded file still above target and
    # drops its entry fails naming the required pair: the branch-point
    # tolerance is for unchanged paths only.
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'record policy')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\nx\n')
    write_records(repo, {})
    commit(repo, 'pr grows the file and drops its entry')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '+ x.py: required [40, 100]' in result.stderr


def test_deleted_records_file_cannot_drop_a_changed_entry(repo):
    # Deleting the records file wholesale does not admit the drop either.
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    target = write_records(repo, {'x.py': [40, 100]})
    base = commit(repo, 'record policy')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\nx\n')
    target.unlink()
    commit(repo, 'pr grows the file and deletes the records')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '+ x.py: required [40, 100]' in result.stderr


def test_deleting_an_empty_records_file_fails(repo):
    target = write_records(repo, {})
    base = commit(repo, 'adopt an empty records file')
    target.unlink()
    (repo / 'y.py').write_text('y = 1\n')
    commit(repo, 'pr deletes the empty records file')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert DELETED in result.stderr


def test_a_second_merge_base_holding_the_records_binds_the_deletion(repo):
    git(repo, 'branch', '-M', 'main')
    git(repo, 'checkout', '-q', '-b', 'pr')
    target = write_records(repo, {'x.py': [3, 6]})
    adopt = commit(repo, 'adopt')
    git(repo, 'checkout', '-q', 'main')
    (repo / 'y.py').write_text('y = 1\n')
    other = commit(repo, 'main moves without records')
    git(repo, 'merge', '-q', '--no-ff', '--no-edit', adopt)
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'pr')
    git(repo, 'merge', '-q', '--no-ff', '--no-commit', other)
    target.unlink()
    (repo / 'x.py').write_text('x = 1\n')
    commit(repo, 'criss-cross merge drops the records')
    assert len(git(repo, 'merge-base', '--all', base, 'HEAD').split()) == 2
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert DELETED in result.stderr


def test_a_merge_that_keeps_readopted_records_passes(repo):
    (repo / 'x.py').write_text('x = 1\n')
    target = write_records(repo, {})
    commit(repo, 'adopt')
    git(repo, 'branch', '-M', 'main')
    git(repo, 'checkout', '-q', '-b', 'pr')
    (repo / 'a.py').write_text('a = 1\n')
    keeps = commit(repo, 'pr side keeps the records')
    git(repo, 'checkout', '-q', 'main')
    git(repo, 'rm', '-q', str(target))
    (repo / 'b.py').write_text('b = 1\n')
    drops = commit(repo, 'main drops the records')
    git(repo, 'merge', '-q', '--no-ff', '--no-edit', keeps)
    write_records(repo, {})
    base = commit(repo, 'main readopts the records')
    git(repo, 'checkout', '-q', 'pr')
    git(repo, 'merge', '-q', '--no-ff', '--no-edit', drops)
    (repo / 'z.py').write_text('z = 1\n')
    commit(repo, 'pr adds a file')
    assert len(git(repo, 'merge-base', '--all', base, 'HEAD').split()) == 2
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 0, result.stderr
    assert DELETED not in result.stderr
