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
[Contributing](CONTRIBUTING.md#tighten-this-repositorys-records)), and give
the action its deploy key (below). Its `push:` filter names both conventional
default branches, `main` and `master`, and its steps and environment resolve
the actual default branch at run time, so the file is the same in a `main`
and a `master` repository; a default branch with any other name is added to
that `branches:` list. Consumers' copies are byte-identical except the step's
`with:` block, if they change it at all. The file may also be one a
consumer's own workflow calls as a reusable workflow: the `workflow_call`
trigger makes that adoption an overwrite of that file too. The action
reference is pinned to the v3.0.1 release commit.

```yaml
name: comment share
on:
  pull_request:
  push:
    branches: [main, master]
  workflow_call:
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow == 'comment share' && format('comment-share-{0}', github.event_name == 'push' && format('{0}-{1}', github.ref, github.sha) || github.ref) || format('comment-share-called-{0}', github.run_id) }}
  cancel-in-progress: true
jobs:
  comment-share:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment: ${{ github.event_name == 'push' && github.ref_type == 'branch' && github.ref_name == github.event.repository.default_branch && format('{0}-push', github.event.repository.default_branch) || '' }}
    steps:
      - if: github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref_type == 'branch' && github.ref_name == github.event.repository.default_branch)
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0
          persist-credentials: false
      - if: github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref_type == 'branch' && github.ref_name == github.event.repository.default_branch)
        uses: Nitjsefnie-Actions/comment-ratchet@30ef8468e4599e10bb76b3916f9c2817a407e910 # v3.0.1
        with:
          target: '15'
          records: '.github/comment-share.json'
          mode: 'auto'
          base-ref: ${{ github.event.pull_request.base.sha }}
          require-tight: 'false'
          push-key: ${{ secrets.MASTER_PUSH_DEPLOY_KEY }}
```

The `with:` block shows **every input with its default**, except `push-key`,
which reads the deploy key. Omit the block and a bare `uses:` step behaves the
same on a pull request. The steps run only on a pull request or a push to the
repository's default branch; on any other event the block runs on, such as a
push to `master` where `main` is the default, the job completes green without
measuring. A standalone pull request run shares its group with that pull
request's earlier run and replaces it. A standalone push run's group is its
branch and commit, so the run the action's own records commit starts cancels
neither the run that pushed it nor an earlier commit's run, running or
pending; an older run whose records push is then refused passes (below).
A called run sees the caller's event, workflow name and run id, so its group
is `comment-share-called-` followed by the calling run's `github.run_id`; a
caller workflow named `comment share`, in any letter case, would share the
standalone group instead, so name it anything else. The checkout selects the
PR's head tree, or the pushed commit; full history supplies the PR base
without retaining a token. The action needs a Git checkout, bash, and access
to npm and PyPI; it sets up Python 3.15.1 and Node 24.17.0 itself. The suite
is configured for Ubuntu and macOS runners. Windows is outside this action's
tested runner set.

`auto` checks on `pull_request` and tightens on a `push` to the repository's
actual default branch. Other events, including pushes to another branch that
reach the action without the steps' `if:`, fail closed and name `mode` and
the event. Force `check` or `tighten` for a manual workflow. A forced check
must supply `base-ref` when the event has no PR base SHA.

**The default branch commits its own records.** On a default-branch push with
a non-empty `push-key`, the action first requires the checkout's `HEAD` to be
`github.sha`, failing without a push otherwise, then tightens over that tree.
When tighten changes nothing, nothing is pushed, so the records commit's own
run pushes nothing. Otherwise the action commits the records file alone on
top of `github.sha`, as `github-actions[bot]` with the message
`ci: tighten comment-ratchet records`, and pushes it as a fast-forward of the
default branch. A refused push passes with a notice when the default branch
has moved past `github.sha` to a tip that contains it: the newer push's run
measures again, and a records file is never merged or rebased. A refusal while
the branch stands at `github.sha`, or at any tip that does not contain it,
fails the run and names the refusal and the tip. A branch that cannot be read
after the refusal, a deleted one included, fails the run naming the refusal
and the read error. The tip is fetched into a scratch repository that borrows
the checkout's objects, so reading it adds nothing to the checkout's objects
or refs and leaves its `FETCH_HEAD` alone.

**The key.** `push-key` takes an SSH write deploy key from the environment
secret `MASTER_PUSH_DEPLOY_KEY` in the `<default branch>-push` environment,
whose deployments are limited to the default branch. The key is the
default-branch ruleset's bypass actor (`DeployKey`): the records commit
carries no checks yet, and the built-in token cannot be a bypass actor. Keep it
out of repository secrets, which any branch's workflow can read. The job's
`environment:` binds that environment only on a default-branch push, so a
pull-request run binds none and the branch policy never refuses it; pull
requests, fork pull requests included, never push. Only the push step
receives the key, and none of its git calls inherits it in the environment;
ssh reads it from a temporary file. GitHub's SSH host keys are pinned from its
Meta endpoint.
An environment secret has been observed to read empty inside a
`workflow_call` callee, so a called run is expected to take the no-key path
below; the block's own push run is the one that commits.

**Without a key**, a default-branch push still tightens the checkout. A
called run, whose workflow name is not `comment share` in any letter case (the
test the concurrency group uses), never fails on the entries tighten changed,
whatever `require-tight` says: each is a `::notice::` saying the standalone
`comment share` run commits it. In the standalone run, under
`require-tight: 'false'`, each entry tighten changed is a `::warning::` naming
`push-key` as unset; under `'true'`, those entries fail the run.

A consumer that delivers comment-share records from its own workflow today
deletes that delivery and its tests; a push workflow that also lands other
records files keeps those. A consumer that wants the check inside a required
aggregate keeps its own aggregate job and points it at this action, as this
repository does.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `target` | `'15'` | Positive integer percent allowed for an unrecorded file. Zero, negative, fractional and non-integer values fail. |
| `records` | `'.github/comment-share.json'` | Repository-relative records file; an empty path, a directory, or a path escaping the checkout fails. |
| `mode` | `'auto'` | Event dispatch above, or exactly `check` / `tighten` to force a mode. |
| `base-ref` | `${{ github.event.pull_request.base.sha }}` | Git revision whose records bind a check. Required for check, unused by tighten. |
| `require-tight` | `'false'` | `'true'` fails a check whose head records differ from what tighten writes over the changed paths, and a keyless default-branch push whose records tighten changes in the standalone `comment share` run (a called run only notes them); `'false'` leaves both to the default branch's run. Any other value fails. |
| `push-key` | `''` | SSH write deploy key a default-branch push commits the tightened records with; empty pushes nothing. |

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
with `npm ci` against committed integrity hashes; ruamel.yaml installs from an
exact version and SHA-256 pin, wheel only, with `pip install --require-hashes`
in an isolated venv. Consumer `PYTHONPATH` is excluded.

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
report naming the path and the byte pair it should hold. Under
`require-tight: 'false'`, the default, a changed recorded file may instead
keep its base pair: that entry is a notice naming what tighten writes, and the
default branch's run lands it. Unrecorded or
over-record files are judged only where the pull request changes them: a
changed recorded file fails when it exceeds the effective limit below on
both dimensions, or when it exceeds its committed record on both dimensions;
and a changed file above the target with no record fails naming its pair —
the repair is trimming the file, never recording it. Renaming a recorded
file above the target is the same change — the new name is unrecorded — so
the file must come back below the target. A file the pull request
does not change is never failed, whatever the merged tree measures: one that
sits above its record on both dimensions is named, with both pairs and the
repair path, as a notice, and one left above the target with no entry by a
merge skew is named with the exact entry a records-only pull request may
land. For an unchanged path the head record must be the base pair, any
lower pair (fewer comment bytes at a share not above the record), or what
the branch point held; a raising or
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
limits above. A pull request whose head lacks the records file fails naming
it when merging HEAD into `base-ref` (`git merge-tree --write-tree`, Git 2.38
or later) would drop the file; it holds `{}` when it records nothing.
That verdict holds for a merge or a squash, not for a commit-by-commit
replay: a branch cut before the base adopted the file, which adds the
identical file and later deletes it, passes, yet `git rebase` onto the base
replays the deletion onto the base's copy, leaving no records file (an add
commit holding only that file is dropped as already upstream). In a hosted
test on 2026-11-01, github.com's rebase merge refused a pull request whose
add commit held only that file (`405 Base branch was modified`), so the
exposed route is a local rebase pushed straight to the default branch: land
pull requests through the pull request, not by pushing a local rebase.

**Tighten** seeds an absent records file in one run. For existing records, it
rewrites a pair only when measured comment bytes are fewer and the measured
share is equal or lower. It adds newly above-target files and drops deleted
files or files at or below target. Tighten never raises an existing record.
Code-only additions, removals or replacements leave the records file
byte-identical unless a file graduates to or below target, when its entry is
dropped, or an unrecorded file moves above target, when its entry is added.
Tighten writes changes into the checkout; on a default-branch push with a
`push-key`, the action commits them itself.

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
and the merge sits above the target with no entry. For a file the pull
request does not change, the repair is records-only: land the entry the
notice names, or lower an untouched record (a raising or loosening
records-only edit fails naming the base pair). For a file the pull request
does change, the repair is trimming the file below the target — recording is
never a repair — or bringing it back within the effective limit; under
`require-tight: 'true'` the same pull request also lands the lowered record
when the reduction lowers one.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the regression suite and the
records-update procedure, and [SECURITY.md](SECURITY.md) for private
vulnerability reporting. Licensed under the [MIT License](LICENSE).
