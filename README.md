# comment-ratchet

Gate the comment share of each tracked source file. The default target is 15%:
files already above it may keep their recorded share, and those records only
tighten. Shares compare exact integer byte pairs, without rounding.

The measurement and ratchet are ported from
[claudit](https://github.com/Nitjsefnie/claudit)'s `scripts/ci/comment_share.py`
and `comment_baseline.py`.

## Install

Save this workflow as `.github/workflows/comment-share.yml`. Change `main` to
your default branch. The action reference is pinned to the v1.0.0 release
commit.

```yaml
name: comment share
on:
  pull_request:
  push:
    branches: [main]
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
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: Nitjsefnie-Actions/comment-ratchet@721426819be9e05deb76d56364f671b87fa05fbf # v1.0.0
        with:
          target: '15'
          records: '.github/comment-share.json'
          mode: 'auto'
          base-ref: ${{ github.event.pull_request.base.sha }}
```

The `with:` block shows **every input with its default**. Omit it and a bare
`uses:` step behaves the same on these events. The checkout selects the PR's
head tree; full history supplies the PR base without retaining a token.
The action needs a Git checkout, bash, and access to npm and PyPI; it sets up
Python 3.13.14 and Node 24.17.0 itself. The suite is configured for Ubuntu and
macOS runners. Windows is outside this action's tested runner set.

`auto` checks on `pull_request` and tightens on a `push` to the repository's
actual default branch. Other events, including pushes to another branch,
fail closed and name `mode` and the event. Force `check` or `tighten` for a
manual workflow. A forced check must supply `base-ref` when the event has no
PR base SHA.

Tighten writes the records file into the checkout; it does not commit or push
it. A consumer's existing ratchet job can commit that result. For initial
adoption, run tighten on the proposed head and commit the seed in the adoption
PR. This repository tightens its records on every default-branch push; a
separate ratchet-push workflow lands the result, as described in
[CONTRIBUTING.md](CONTRIBUTING.md#tighten-this-repositorys-records).

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
| SQL | `.sql` | PostgreSQL lexical comments: `--` lines and nested `/* */` blocks. Single-quoted strings (backslashes escape only in `E'…'`), double-quoted identifiers and case-sensitive dollar-quoted bodies remain code; unfinished quotes or block comments fail measurement. Uses `standard_conforming_strings` on. |
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
version and SHA-256 wheel pins with `pip install --require-hashes` in an
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
edits to that file. If the base object is absent locally, it performs a targeted
`git fetch --no-tags origin <base-ref>` in the **consumer checkout**, without
changing HEAD. Each unrecorded file above target and each recorded file above
its record fails, with its path and both byte pairs. Equal or lower shares,
deleted files and files back at or below target pass.

**When the base has no records file**, check falls back to the head commit's
records file. That seed must contain **exactly** the entries and byte pairs
that tightening the measured head from scratch would write: nothing looser,
nothing missing, no extra entries, and no substituted equivalent fraction.
Every differing entry is named. If neither revision has the file, check fails
and tells you to run tighten to seed one and commit it. Once the base has a
records file, **only that base file binds**; a head edit cannot loosen it.

**Tighten** seeds an absent records file in one run. For existing records, it
lowers each pair to the measured pair when the share is equal or lower, keeps
the old pair when the share rose, adds newly above-target files, and drops
deleted or graduated files. Tighten never raises an existing record; it writes
the result for the caller to commit.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the regression suite and the
records-update procedure, and [SECURITY.md](SECURITY.md) for private
vulnerability reporting. Licensed under the [MIT License](LICENSE).
