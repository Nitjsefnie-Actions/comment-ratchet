# comment-ratchet

Gate the comment share of each tracked source file. The default target is 15%:
files already above it may keep their recorded share, and those records only
tighten. Shares compare exact integer byte pairs, without rounding.

The measurement and ratchet are ported from
[claudit](https://github.com/Nitjsefnie/claudit)'s `scripts/ci/comment_share.py`
and `comment_baseline.py`.

## Install

Save this workflow as `.github/workflows/comment-share.yml`. It is the whole
adoption: save the file, seed the records in the adoption pull request by
running tighten on the proposed head (the command is under
[Contributing](CONTRIBUTING.md#tighten-this-repositorys-records)), and no
other workflow, step, credential or branch edit is needed. The block triggers
only on `pull_request`, so your default branch's name appears nowhere in the
file and no push ever starts a run. Consumers' copies are byte-identical
except the step's `with:` block, if they change it at all. The action
reference is pinned to the v1.2.1 release commit.

```yaml
name: comment share
on:
  pull_request:
permissions:
  contents: read
concurrency:
  group: comment-share-${{ github.ref }}
  cancel-in-progress: true
jobs:
  comment-share:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: Nitjsefnie-Actions/comment-ratchet@8433cde403c63b6494b05f5de4244d0fd2592f4f # v1.2.1
        with:
          target: '15'
          records: '.github/comment-share.json'
          mode: 'auto'
          base-ref: ${{ github.event.pull_request.base.sha }}
```

The `with:` block shows **every input with its default**. Omit it and a bare
`uses:` step behaves the same on a pull request. The checkout selects the
PR's head tree; full history supplies the PR base without retaining a token.
The action needs a Git checkout, bash, and access to npm and PyPI; it sets up
Python 3.15.1 and Node 24.17.0 itself. The suite is configured for Ubuntu and
macOS runners. Windows is outside this action's tested runner set.

`auto` checks on `pull_request` and tightens on a `push` to the repository's
actual default branch, though the install block never sees those pushes: it
triggers only pull requests. Other events, including pushes to another
branch, fail closed and name `mode` and the event. Force `check` or `tighten`
for a manual workflow. A forced check must supply `base-ref` when the event
has no PR base SHA.

**Records are maintained by pull requests.** Tighten writes the records file
into the checkout only when you run it yourself, to seed or repair records;
no workflow commits or pushes them. A pull request whose head tree makes
tighten change the base records — comment bytes fewer at an equal or lower
share, a deleted file, a file at or under the target — carries that change:
its check compares the records at HEAD with what tighten would write over the
changed paths and fails naming each entry and the byte pair it should hold,
so one pull request can land the repair. If your repository delivers records
from a push job today, delete that arm; a push workflow that also lands other
records files keeps those, as this repository's own does for its size
baseline ([CONTRIBUTING.md](CONTRIBUTING.md#tighten-this-repositorys-records)).
A consumer that wants the check inside a required aggregate keeps its own
aggregate job and points it at this action, as this repository does.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `target` | `'15'` | Positive integer percent allowed for an unrecorded file. Zero, negative, fractional and non-integer values fail. |
| `records` | `'.github/comment-share.json'` | Repository-relative records file; an empty path, a directory, or a path escaping the checkout fails. |
| `mode` | `'auto'` | Event dispatch above, or exactly `check` / `tighten` to force a mode. |
| `base-ref` | `${{ github.event.pull_request.base.sha }}` | Git revision whose records bind a check. Required for check, unused by tighten. |

Invalid inputs fail the run and name the input. Inputs reach the action through
environment variables. They are never interpolated into shell programs.

## Measurement rules

Only Git-tracked files are measured; untracked files and unsupported languages
are ignored. Counts are the **UTF-8 bytes of comments divided by all
non-whitespace UTF-8 bytes**, per file. Both counts exclude characters for
which Python's `str.isspace()` is true, including Unicode whitespace. Comment
delimiters count as comment bytes. An empty file has share zero.

| Language | Files | What counts as a comment |
| --- | --- | --- |
| Python | `.py` | `COMMENT` tokens and string tokens in bare string expressions, including docstrings, prefixes and quotes. Assigned strings, bytes literals and f-strings remain code. |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | Espree comment ranges, including hashbangs. String/template/regex contents and JSX text remain code. JSX parsing is enabled for `.jsx`. |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` (including `.d.ts`) | TypeScript compiler comment trivia, including hashbangs and comments in JSX expressions. String/template/regex contents and JSX text remain code. JSX parsing is enabled only for `.tsx`; any compiler parse diagnostic fails measurement. |
| SQL | `.sql` | PostgreSQL lexical comments: `--` lines and nested `/* */` blocks. Single-quoted strings (backslashes escape in `E'…'` and its newline-separated continuations), double-quoted identifiers and case-sensitive dollar-quoted bodies remain code; unfinished quotes or block comments fail measurement. Line comments in continuation gaps count and preserve string mode; block comments break continuation. Uses `standard_conforming_strings` on. |
| YAML | `.yaml`, `.yml` | Lexical YAML comments and block-scalar header comments. Block scalar values of lexically resolved `run` keys are also measured as shell, including workflow `run: |` and `run: >` bodies. Other scalar contents remain data. |
| Shell | `.sh`, or extensionless files with a recognized shell shebang | Lexical shell comments outside quotes, parameter text and heredoc bodies; command substitutions have their own scopes. |

Recognized shebang interpreters are `sh`, `bash`, `dash`, `ash`, `ksh`, `mksh`
and `zsh`, including `env` forms. This is lexical measurement, not execution or
a full shell grammar. The port supports ordinary and ANSI-C quotes, escapes,
command/process/backtick/parameter scopes, quote-free arithmetic, here-strings
and ordered literal-word heredocs. It refuses constructs it cannot classify:
for example mixed or ANSI-C quoted heredoc delimiters, quotes inside arithmetic,
`case` words inside substitutions, `[[ =~ ]]` operands, unsupported word-glued
parentheses, and unresolved aliased/property-prefixed YAML block keys.
Malformed source, invalid UTF-8, unreadable files and unfinished scopes fail the
run with the filename. Fix or rewrite that source before retrying the gate.

The action parses source as data. Python imports, JavaScript/TypeScript helpers,
requirements and the npm lockfile all come from the action's own directory;
consumer modules and package install scripts are not executed. Espree and TypeScript install
with `npm ci` against committed integrity hashes; PyYAML installs from exact
version and SHA-256 pins, from source on CPython 3.15 until upstream ships
wheels, with `pip install --require-hashes` in an
isolated venv. Consumer `PYTHONPATH` is excluded.

## Ratchet and records

`.github/comment-share.json` is a JSON object mapping tracked paths to
`[comment_bytes, non_whitespace_bytes]`. For example, `# hi` followed by `x=1`
in `example.py` has this record:

```json
{
  "example.py": [3, 6]
}
```

The writer sorts keys, uses two-space indentation and ends with a newline.
Only files above the configured target need records. Byte pairs retain their
exact values; `current_comments * recorded_total > recorded_comments *
current_total` is a rise, even if a rounded percentage would look equal.

**Check** reads the records file at `base-ref` with Git, independent of head
edits to that file, and reads the records at HEAD beside them. If the base
object is absent locally, it performs a targeted
`git fetch --no-tags origin <base-ref>` in the **consumer checkout**, without
changing HEAD. The pull request's changed set is the merge-base diff
(`git merge-base base-ref HEAD`), so a pull request behind main is not
judged on paths main moved after the branch point; records policy itself
stays at `base-ref`. The head records must hold exactly the entries tighten
writes over the changed paths from the base records: unchanged files carry
their recorded pair byte-for-byte; a deleted file's entry is dropped; a
changed file at or under the target drops its entry; and a changed recorded
file keeps its pair unless tighten lowers it (fewer comment bytes at a share
not above the record). Each differing entry fails as a `+`/`~`/`-` diff
report naming the path and the byte pair it should hold. Unrecorded or
over-record files are judged only where the pull request changes them: a
changed recorded file fails when it exceeds the effective limit below on
both dimensions, or when it exceeds its committed record on both dimensions;
and a changed file above the target with no record fails naming its pair —
the repair is trimming the file, never recording it. A file the pull request
does not change is never failed, whatever the merged tree measures: one that
sits above its record on both dimensions is named, with both pairs and the
repair path, as a notice, and one left above the target with no entry by a
merge skew is named with the exact entry a records-only pull request may
land. For an unchanged path the head record must be the base pair, a
tighten-lowering of it, or what the branch point held; a raising or
loosening records-only edit fails naming the base pair. See the two repair
paths after **Tighten**. The judgment reads the committed `base-ref`..`HEAD`
diff only: an edit that exists just in an uncommitted working tree is not
part of any pull request and is not judged.

The effective share limit never exceeds the committed record's share. Code
added on the default branch tightens later pull requests while it stays there.
If that code is later deleted, check accepts a loosening: with record
`[40,100]`, a branch that goes to `(40,200)` and then `(40,120)` allows a head
of `(50,150)` at the base's share. A previously tightened record of `[40,200]`
would reject that head.

**When the base has no records file**, check falls back to the head commit's
records file. That seed must contain **exactly** the entries and byte pairs
that tightening the measured head from scratch would write: nothing looser,
nothing missing, no extra entries, and no substituted equivalent fraction.
Every differing entry is named. If neither revision has the file, check fails
and tells you to run tighten to seed one and commit it. Once the base has a
records file, head edits cannot replace the base records or the effective
limits above.

**Tighten** seeds an absent records file in one run. For existing records, it
rewrites a pair only when measured comment bytes are fewer and the measured
share is equal or lower. It adds newly above-target files and drops deleted
files or files at or below target. Tighten never raises an existing record.
Code-only additions, removals or replacements leave the records file
byte-identical unless a file graduates to or below target, when its entry is
dropped, or an unrecorded file moves above target, when its entry is added.
Tighten writes changes for the caller to commit.

**Tighten reports, it never fails and never raises.** A recorded file sitting
above its record on both dimensions — comment bytes and share — is printed to
the log and emitted as a `::warning::` annotation, naming the file, the
measured pair and the record pair, and pointing at the same repair path: a
pull request touching the file must bring it back within the effective limit,
because tighten never raises a record. The run's exit status and write
behavior are unchanged by these reports, so a default-branch push stays green
while a loose record waits for a pull request to repair it.

**Two repairs a pull request can land.** Two green pull requests can merge
into a tree tighten would change: one graduates a file and drops its entry
while another, branched earlier, adds comments within that file's old record,
and the merge sits above the target with no entry. A pull request that does
not change that file is never failed for the skew, and the pull request that
changes the file carries the tightened entry its check names. Separately, a
file whose merged pair exceeds its tightened record — tighten never raises
one — gets the second repair: a pull request touching that file must bring
it back within the effective limit, reducing its comment bytes or its share
and landing the lowered record in the same pull request when the reduction
lowers the record.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the regression suite and the
records-update procedure, and [SECURITY.md](SECURITY.md) for private
vulnerability reporting. Licensed under the [MIT License](LICENSE).
