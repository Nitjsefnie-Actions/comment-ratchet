# Contributing to comment-ratchet

Issues and pull requests are welcome, especially a small source fixture whose
measured byte pair or ratchet verdict differs from the README. Include the
source, language, expected counts and the actual command output.

## LLM and agent contributions are welcome

You may use an LLM or coding agent. There is no separate review queue or
expectation that you rewrite its output by hand. Two conditions apply:

1. Disclose the serving model in each commit it authored:

   ```
   Co-Authored-By: <Model Name> <noreply@example.com>
   ```

   Use the plain model name, without a context-window suffix. For GPT-6.1 Sol,
   use `Co-Authored-By: GPT-6.1 Sol <noreply@openai.com>`. Inspect all outgoing
   commits, not only the tip:

   ```bash
   git log --format='%h %(trailers:key=Co-Authored-By,valueonly)' origin/main..HEAD
   ```

2. Verify every behavioral claim. Paste the command, actual output and exit
   status. A suite piped through another command can hide its failure.

## A new file is invisible until you name it

`.gitignore` denies by default and names back the files this repository ships.
Reopen a directory before allowing files inside it:

```
*
!tests/
tests/*
!tests/run.sh
```

After changing ignore rules, seed a junk file in each kept directory, prove
`git check-ignore -v` ignores it, and verify every intended file is tracked.

## Getting it running

Use Python 3.13.14, Node 24.17.0, Git, bash and ShellCheck. Install the pinned
toolchain into a venv; use the committed npm lockfile:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --require-hashes --only-binary=:all: -r requirements-test.txt
npm ci --ignore-scripts --no-audit --no-fund
tests/run.sh
shellcheck tests/run.sh
```

The suite ports claudit's measurement fixtures and ratchet boundaries, then
exercises the CLI against real disposable Git repositories. It covers exact
seed fallback, base-file authority, lower-only tightening, targeted fetch,
input validation, event dispatch, isolated consumer imports and the aggregate
verdict. Temporary repositories configure their own test identity; they never
change this checkout's commit identity. Run suites bare and read their actual
exit status.

The action installs its own dependencies and sets up Python and Node. To test
runner integration, install a branch in a disposable consumer repository and
run its PR workflow. Local subprocess tests do not replace a hosted run.

## Tighten this repository's records

Records tighten on every default-branch push. After the entire tests gate is
green on main, its keyless ratchet job runs the local action in tighten mode.
Only a changed records file stages a ratchet-push artifact, together with the
measured run's head SHA in base.txt.

The separate top-level [ratchet-push workflow](.github/workflows/ratchet-push.yml)
lands the result. It admits only successful main push runs, downloads that
run's data, and creates a records-only commit with the environment deploy key.
It checks out no code, installs nothing, and runs no fetched helpers. Configure
MASTER_PUSH_DEPLOY_KEY in the main-push environment; a missing key fails loudly.
The bot commit reruns the gate once, and unchanged records stage no artifact,
so the chain stops there.

When main has moved, the push job merges the single records file three ways.
Conflicts drop that run with a summary; a push rejected while main stood still
is an error. A rejection after main moved drops the stale run so the next gate
can measure again.

For initial adoption, generate the exact seed for the proposed head and commit
it with the PR, because a seedless base uses the head seed under the strict
fallback rule:

```bash
COMMENT_RATCHET_MODE=tighten GITHUB_WORKSPACE="$PWD" python3 -I run.py
git add .github/comment-share.json
```

After adoption, head edits never replace the base policy for a check. The
push workflow lowers existing records and cannot cure a regression by raising
them.

## Python coverage comments

The Ubuntu suites cell measures run.py and scripts/ci/*.py, including isolated
Python subprocesses, using the hash-pinned coverage toolchain. macOS runs the
same regression suite without measurement. Coverage is informational: there
is no new coverage floor or required-checks file.

For a green Ubuntu PR suite, the rendered patch-coverage report and PR number
are uploaded as data. The report covers the lines the pull request adds or
changes and names missed lines. The separate coverage-comment workflow holds
the conversation write token, resolves the destination from the triggering
event, validates the artifact's PR number, and posts or updates one comment.
It checks out no PR code and executes nothing from the artifact. Missing
measurement replaces an existing stale percentage with a not-measured marker.

## House style

- Comments explain why. Keep the workflow comments that record supply-chain,
  permission, trigger and exit-status decisions accurate when changing a step.
- Inputs are environment data. Keep `${{ inputs.* }}` out of shell program
  text, and execute only the action's code and pinned parser dependencies.
- Preserve the port's lexical byte-count semantics. Add literal source and
  byte-pair fixtures for a measurement change; keep integer cross-products.
- Test behavior rather than prose. Do not pin README wording. For a bug fix,
  first reproduce the failure with a test that fails before the fix.

## Commit messages

Use Conventional Commits with a required scope:

```
<type>(<scope>): <summary>

<body explaining why, when needed>

Co-Authored-By: <Model Name> <noreply@example.com>
```

The summary is imperative, lower case, has no trailing full stop and fits in
72 characters. One logical change per commit.

| Type | Use |
| --- | --- |
| `feat` | New behavior a caller can observe |
| `fix` | A caller-visible defect |
| `docs` | Documentation |
| `test` | Regression cases or test machinery |
| `ci` | Workflows, Dependabot or automation |
| `refactor` | Structure without behavior changes |
| `perf` | A measured optimization |
| `chore` | Repository housekeeping |
| `revert` | A named earlier change |

| Scope | Subject |
| --- | --- |
| `action` | Inputs, dispatch and `action.yml` |
| `ratchet` | Measurement, records and policy |
| `harness` | Test machinery |
| Workflow name | That workflow, with type `ci` |
| `deps` | Dependency bumps, including Dependabot |
| `readme`, `contributing`, `security`, `coc` | The named document |
| `templates`, `repo` | Repository forms or furniture |

A workflow's name is a scope only with type `ci`: `ci(tests)` changes the
workflow, while `test(harness)` changes test machinery. Use `ci(deps)` for
dependency updates. Breaking changes use both `!` and a `BREAKING CHANGE:`
footer explaining the caller impact. Releases follow semantic versioning:
breaking changes bump major, features minor, and other fixes patch.

## Issues

Use the [issue form](.github/ISSUE_TEMPLATE/issue.md). Its Description records
observed behavior; a proposed mechanism or fix belongs under Suggested Fix,
marked unverified. Omit reproduction steps that do not reliably reproduce it.
Exploitable input execution or ratchet bypass belongs in a
[private security advisory](https://github.com/Nitjsefnie-Actions/comment-ratchet/security/advisories/new).

### Claim it before you start

Comment `/claim` on an open unassigned issue, then confirm your login appears
in its assignees. This repository's claim workflow uses the separate
[claim action](https://github.com/Nitjsefnie-Actions/claim). Release an issue
you stop working on before it closes.

## One maintainer, on purpose

[@Nitjsefnie](https://github.com/Nitjsefnie) maintains this project. Security
response commitments depend on that maintainer's availability; see
[SECURITY.md](SECURITY.md).

## Pull requests

Keep changes small and single-purpose. Claim the issue first, name it in the
PR's Related Issues and Pull Requests section, and follow the repository's PR
template. Include what changed, why and actual verification output. File a
second defect separately rather than silently expanding the patch.
