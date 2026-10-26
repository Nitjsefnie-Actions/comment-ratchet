"""Merge-skew, rename repair and over-record notice paths."""
from __future__ import annotations

import json

from tests.test_ci_comment_baseline import cli, commit, git, init_repo, repo, write_records


def skewed_repo(tmp_path, shrink, add):
    """Two green branches merge into a tree whose records lag the tree."""
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    source = origin / 'a.py'
    # (40, 125); line 3 keeps branch edits apart.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 125]})
    commit(origin, 'base')
    git(origin, 'branch', '-M', 'main')
    # Each branch commits before its check: the asserts judge its own diff.
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
    # brA graduates a.py; brB (branched earlier) adds comments within the old
    # record: the merge sits above target with no entry, and an untouched PR
    # passes.
    def shrink(origin):
        source = origin / 'a.py'
        source.write_text('#' + 'c' * 14 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')  # (15, 100): at target, graduates
        write_records(origin, {})

    def add(origin):
        with (origin / 'a.py').open('a') as handle:
            handle.write('#' + 'c' * 3 + '\n' + 'x' * 13 + '\n')

    origin = skewed_repo(tmp_path, shrink, add)
    # The merge holds the skew: (19, 117), no entry.
    assert json.loads((origin / '.github/comment-share.json').read_text()) == {}
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    commit(origin, 'a pull request that does not touch a.py')
    result = cli(origin, '--base-ref', git(origin, 'rev-parse', 'HEAD~1'))
    assert result.returncode == 0, result.stderr

def test_touching_the_skewed_file_names_the_entry_to_carry(tmp_path):
    def shrink(origin):
        source = origin / 'a.py'
        source.write_text('#' + 'c' * 14 + '\n' + 'x' * 60 + '\n' + 'x' * 25 + '\n')  # (15, 100): at target, graduates
        write_records(origin, {})

    def add(origin):
        with (origin / 'a.py').open('a') as handle:
            handle.write('#' + 'c' * 3 + '\n' + 'x' * 13 + '\n')

    origin = skewed_repo(tmp_path, shrink, add)
    with (origin / 'a.py').open('a') as handle:
        handle.write('x\n')
    commit(origin, 'a pull request that touches a.py')
    result = cli(origin, '--base-ref', git(origin, 'rev-parse', 'HEAD~1'))
    assert result.returncode == 1
    assert '+ a.py: required [19, 118]' in result.stderr
    assert 'exceeds' not in result.stderr
    write_records(origin, {'a.py': [19, 118]})
    commit(origin, 'carry the entry')
    # The PR base is the skew-holding merge, one commit back.
    assert cli(origin, '--base-ref', git(origin, 'rev-parse',
                                         'HEAD~2')).returncode == 0

def test_merged_pair_exceeding_the_record_fails_touching_prs(tmp_path):
    # brA raises the share with no new comments; brB adds comments at an
    # equal share: both pass, and the merge exceeds the record tighten cannot
    # raise.
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    source = origin / 'a.py'
    # (40, 110); line 3 keeps branch edits apart.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 60 + '\n' + 'x' * 10 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 110]})
    base0 = commit(origin, 'base')
    git(origin, 'branch', '-M', 'main')

    git(origin, 'checkout', '-q', '-b', 'brA')
    # (40, 90): raised share, no new comments, nothing to carry.
    source.write_text('#' + 'c' * 39 + '\n' + 'x' * 40 + '\n' + 'x' * 10 + '\n')
    commit(origin, 'branch a')
    first = cli(origin, '--base-ref', base0)
    assert first.returncode == 0, first.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brA')
    git(origin, 'checkout', '-q', '-b', 'brB', base0)
    # Appended (4, 12): brB's share stays at or below base's.
    with source.open('a') as handle:
        handle.write('#' + 'c' * 3 + '\n' + 'x' * 12 + '\n')
    commit(origin, 'branch b')
    second = cli(origin, '--base-ref', base0)
    assert second.returncode == 0, second.stderr
    git(origin, 'checkout', '-q', 'main')
    git(origin, 'merge', '--no-edit', '-q', 'brB')
    # Merge (44, 106): above the record share, so the limit stays [40, 110].
    source = origin / 'a.py'
    base = git(origin, 'rev-parse', 'HEAD')
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    commit(origin, 'untouched')
    assert cli(origin, '--base-ref', base).returncode == 0
    git(origin, 'reset', '-q', '--hard', base)
    with source.open('a') as handle:
        handle.write('x\n')
    commit(origin, 'touching')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1
    assert 'a.py: (44, 107) exceeds [40, 110]' in result.stderr
    git(origin, 'reset', '-q', '--hard', base)
    # (38, 130): fewer comments at a lower share lowers and lands the record.
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
    # --no-renames judges a rename delete+add, so the old entry drops and the
    # new path is required; rename detection would carry the old entry
    # forever.
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

def test_unchanged_over_record_file_named_not_failed(tmp_path):
    # Issue 69: a grown file every gate let past; an untouched pull request
    # passes with the notice.
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'a.py').write_text('#' + 'c' * 44 + '\n' + 'x' * 60 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 100]})
    base = commit(origin, 'main holds a file above its record')
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    commit(origin, 'a pull request that does not touch a.py')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 0, result.stderr
    assert 'a.py: (45, 105) exceeds its record [40, 100]' in result.stderr
    assert 'a pull request touching the file must bring it back' in result.stderr
    assert '::notice::' in result.stdout
    assert 'a.py: (45, 105)' in result.stdout


def test_changed_over_record_file_takes_the_failure_not_the_notice(tmp_path):
    # The base source measures above the record, so no substituted limit
    # masks the notice branch's changed-path conjunct.
    origin = tmp_path / 'origin'
    origin.mkdir()
    init_repo(origin)
    (origin / 'a.py').write_text('#' + 'c' * 44 + '\n' + 'x' * 60 + '\n')
    (origin / 'b.py').write_text('x = 1\n')
    write_records(origin, {'a.py': [40, 100]})
    base = commit(origin, 'main holds a file above its record')
    (origin / 'b.py').write_text('x = 1\ny = 2\n')
    (origin / 'a.py').write_text('#' + 'c' * 44 + '\n' + 'x' * 59 + '\n')
    commit(origin, 'a pull request that touches a.py')
    result = cli(origin, '--base-ref', base)
    assert result.returncode == 1, result.stderr
    assert 'a.py: (45, 104) exceeds its record [40, 100]' in result.stderr
    assert '::notice::' not in result.stdout

