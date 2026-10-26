"""The require-tight records map over changed paths.

Head records must equal what tighten writes from the base records: unchanged
files carry their pair byte-for-byte, changed files carry the tightening, and
the skew sequence of issue 68 item 3 repairs inside one pull request.
"""
from __future__ import annotations

import json

import pytest

from tests.test_ci_comment_baseline import cli, commit, git, init_repo, repo, write_records

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


# The skew sequence of issue 68 item 3: two green branches can merge into a
# tree tighten would change, and the require-tight check repairs it in pull
# requests instead of a default-branch delivery.
def skewed_repo(tmp_path, shrink, add):
    """Two green branches merge into a tree whose records lag the tree."""
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    source = origin / 'a.py'
    # a.py: 40 comments + 85 code = (40, 125); line 3 keeps the branches'
    # edits apart so the merge is clean.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 125]})
    commit(origin, 'base')
    git(origin, 'branch', '-M', 'main')
    # Each branch commits its change before its check, so the two green
    # asserts judge the branches' own diffs, not the untouched parent.
    git(origin, 'checkout', '-q', '-b', 'brA')
    shrink(origin)
    commit(origin, 'branch a')
    first = cli(origin, '--base-ref', git(origin, 'rev-parse', 'main'))
    assert first.returncode == 0, first.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brA')
    git(origin, 'checkout', '-q', '-b', 'brB', 'main~1')
    add(origin)
    commit(origin, 'branch b')
    second = cli(origin, '--base-ref', git(origin, 'rev-parse', 'main~1'))
    assert second.returncode == 0, second.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brB')
    return origin


def test_out_of_date_merge_does_not_wedge_untouched_files(tmp_path):
    # brA graduates a.py and drops its entry while brB, branched earlier,
    # adds comments within the old record; the merge sits above the target
    # with no entry. A pull request that does not touch a.py passes.
    def shrink(origin):
        source = origin / 'a.py'
        source.write_text('#' + 'c' * 14 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')  # (15, 100): at the target, so it graduates
        write_records(origin, {})

    def add(origin):
        with (origin / 'a.py').open('a') as handle:
            handle.write('#' + 'c' * 3 + '\n' + 'x' * 13 + '\n')

    origin = skewed_repo(tmp_path, shrink, add)
    # The merge itself holds the skew: (19, 117) above target, no entry.
    assert json.loads((origin / '.github/comment-share.json').read_text()) == {}
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    commit(origin, 'a pull request that does not touch a.py')
    result = cli(origin, '--base-ref', git(origin, 'rev-parse', 'HEAD~1'))
    assert result.returncode == 0, result.stderr


def test_touching_the_skewed_file_names_the_entry_to_carry(tmp_path):
    def shrink(origin):
        source = origin / 'a.py'
        source.write_text('#' + 'c' * 14 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')  # (15, 100): at the target, so it graduates
        write_records(origin, {})

    def add(origin):
        with (origin / 'a.py').open('a') as handle:
            handle.write('#' + 'c' * 3 + '\n' + 'x' * 13 + '\n')

    origin = skewed_repo(tmp_path, shrink, add)
    # A pull request touching a.py must carry the entry tighten would add.
    with (origin / 'a.py').open('a') as handle:
        handle.write('x\n')
    commit(origin, 'a pull request that touches a.py')
    result = cli(origin, '--base-ref', git(origin, 'rev-parse', 'HEAD~1'))
    assert result.returncode == 1
    assert '+ a.py: required [19, 118]' in result.stderr
    assert 'exceeds' not in result.stderr
    write_records(origin, {'a.py': [19, 118]})
    commit(origin, 'carry the entry')
    # The pull request base is the merge that holds the skew, one commit
    # before the touching commit.
    assert cli(origin, '--base-ref', git(origin, 'rev-parse',
                                         'HEAD~2')).returncode == 0


def test_merged_pair_exceeding_the_record_fails_touching_prs(tmp_path):
    # brA removes code (raising the share without new comments) and brB adds
    # comments at an equal share; both pass, and the merge exceeds the record
    # tighten cannot raise. Untouched pull requests pass; a touching one must
    # reduce the file or, when comments drop below the record at a lower
    # share, land the lowered record.
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    source = origin / 'a.py'
    # a.py: 40 comments, 70 code = (40, 110); line 3 keeps the branches'
    # edits apart so the merge is clean.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n' + 'x' * 10 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 110]})
    base0 = commit(origin, 'base')
    git(origin, 'branch', '-M', 'main')

    git(origin, 'checkout', '-q', '-b', 'brA')
    # 60 -> 40 code on line 2: (40, 90), a raised share with no new comments,
    # so no violation and nothing to carry.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 40 + '\n' + 'x' * 10 + '\n')
    commit(origin, 'branch a')
    first = cli(origin, '--base-ref', base0)
    assert first.returncode == 0, first.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brA')
    git(origin, 'checkout', '-q', '-b', 'brB', base0)
    # The appended (4, 12) keeps brB's share at or below the base's.
    with source.open('a') as handle:
        handle.write('#' + 'c' * 3 + '\n' + 'x' * 12 + '\n')
    commit(origin, 'branch b')
    second = cli(origin, '--base-ref', base0)
    assert second.returncode == 0, second.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brB')
    # The merge: 44 comments, 40 + 10 + 12 code = (44, 106), above the
    # record share, so the effective limit stays the record [40, 110].
    source = origin / 'a.py'
    base = git(origin, 'rev-parse', 'HEAD')
    # A pull request not touching a.py passes.
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    commit(origin, 'untouched')
    assert cli(origin, '--base-ref', base).returncode == 0
    git(origin, 'reset', '-q', '--hard', base)
    # A touching pull request names the repair: reduce the file.
    with source.open('a') as handle:
        handle.write('x\n')
    commit(origin, 'touching')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert 'a.py: (44, 107) exceeds [40, 110]' in result.stderr
    git(origin, 'reset', '-q', '--hard', base)
    # Reducing comments below the record at a lower share lowers the record,
    # and the same pull request lands it: (38, 130).
    source.write_text('#' + 'c' * 37 + '\n' + 'x' * 92 + '\n')
    commit(origin, 'reduce')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert '~ a.py: required [38, 130], recorded [40, 110]' in result.stderr
    write_records(origin, {'a.py': [38, 130]})
    commit(origin, 'land the lowered record')
    assert cli(origin, '--base-ref', base).returncode == 0


def test_deleted_recorded_file_drops_its_entry(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'a.py').write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 100]})
    base = commit(origin, 'base')
    git(origin, 'rm', '-q', 'a.py')
    commit(origin, 'pr deletes a recorded file')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert '- a.py: recorded [40, 100]' in result.stderr
    write_records(origin, {})
    commit(origin, 'carry the drop')
    assert cli(origin, '--base-ref', base).returncode == 0


def test_new_above_target_file_adds_its_measured_pair(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'x.py').write_text('# hi\nx=1\n')
    write_records(origin, {'x.py': [3, 6]})
    base = commit(origin, 'base')
    (origin / 'new.sh').write_text('# yes\n')
    commit(origin, 'pr adds a file above target')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert '+ new.sh: required [4, 4]' in result.stderr
    write_records(origin, {'x.py': [3, 6], 'new.sh': [4, 4]})
    commit(origin, 'carry the new entry')
    assert cli(origin, '--base-ref', base).returncode == 0


def test_records_diff_and_violation_print_in_one_run(repo):
    source = repo / 'x.py'
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    write_records(repo, {'x.py': [40, 100]})
    commit(repo, 'record policy')
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 160 + '\n')
    base = commit(repo, 'default branch adds code')
    source.write_text('#' + 'c' * 44 + '\n' + 'x' * 175 + '\n')
    write_records(repo, {'x.py': [3, 6]})
    commit(repo, 'pr head with a wrong records edit')
    result = cli(repo, '--base-ref', base)
    assert result.returncode == 1
    assert '~ x.py: required [40, 100], recorded [3, 6]' in result.stderr
    assert 'x.py: (45, 220) exceeds [40, 200]' in result.stderr



def test_renamed_recorded_file_moves_its_entry(tmp_path):
    # A rename is judged as delete+add (--no-renames in changed_paths), so a
    # renamed recorded file must move its entry: the check names the old
    # entry's drop and the new path's required pair. Rename detection would
    # list only the new path and carry the old entry forever.
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'a.py').write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 100]})
    base = commit(origin, 'base')
    git(origin, 'mv', 'a.py', 'renamed.py')
    commit(origin, 'pr renames a recorded file')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert '- a.py: recorded [40, 100]' in result.stderr
    assert '+ renamed.py: required [40, 100]' in result.stderr
    write_records(origin, {'renamed.py': [40, 100]})
    commit(origin, 'carry the moved entry')
    assert cli(origin, '--base-ref', base).returncode == 0
