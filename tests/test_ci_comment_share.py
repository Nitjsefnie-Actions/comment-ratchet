from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import comment_share as counter

ROOT = Path(__file__).resolve().parents[1]
ESPREE = Path(os.environ.get("COMMENT_RATCHET_ESPREE_DIR") or ROOT / "node_modules")

CASES = [
    ('py_hash', 'py', 'x = "# code" # hi\n', (3, 12)),
    ('py_bare', 'py', '"mod"\nclass C:\n "cls"\n def f(self):\n  "fun"\n  "bare"\n  return 1\n', (21, 46)),
    ('py_nonstring', 'py', 'f"# {1}"\nb"#"\nx = "#"\n# yes\n', (4, 20)),
    ('py_utf8', 'py', 'é = 1; "dé" # ø\n', (8, 13)),
    ('py_line_separator', 'py', 'x = "\u2028"; "doc" # é\n"next"\n', (14, 19)),
    ('py_concat', 'py', '("a"\n # x\n "b")\n', (8, 10)),
    ('yaml_literal', 'yaml', 'a: |\n  text # data\n', (0, 12)),
    ('yaml_folded', 'yaml', 'a: >\n  text # data\n', (0, 12)),
    ('yaml_literal_header', 'yaml', 'a: | # yes\n  data\n', (4, 11)),
    ('yaml_folded_header', 'yaml', 'a: >-2 # yes\n  data\n', (4, 13)),
    ('yaml_run_literal_header', 'yaml', 'run: |2- # yes\n  echo ok\n', (4, 17)),
    ('yaml_run_folded_header', 'yaml', 'run: > # yes\n  echo ok\n', (4, 15)),
    ('yaml_block_reset', 'yaml', 'a: |\n  "text\nb: 1 # real\n', (5, 16)),
    ('yaml_run', 'yaml', 'run: |\n  # shell\n  cat <<EOF\n  # body\n  EOF\n  echo "multi\n    # string"\nb: 1 # real\n', (11, 53)),
    ('yaml_spaced_quote', 'yaml', "a: this is 'plain # real\n", (5, 19)),
    ('shell_command_scope', 'sh', 'echo "$(echo x # real\n)"\n', (5, 19)),
    ('shell_backtick_scope', 'sh', 'echo "`echo x # real\n`"\n', (5, 18)),
    ('shell_parameter_command', 'sh', 'echo ${x:-$(echo x # literal\n)} # yes\n', (12, 30)),
    ('shell_comment_physical_newline', 'sh', '# first\\\nprintf second\n', (7, 19)),
    ('shell_parameter_nested_command', 'sh', 'echo ${x:-$(echo x # real\n)}\n', (5, 23)),
    ('shell_parameter_nested_backtick', 'sh', 'echo ${x:-`echo x # real\n`}\n', (5, 22)),
    ('shell_parameter_brace', 'sh', 'echo ${x:-foo{} # yes\n', (4, 18)),
    ('shell_parameter_scope', 'sh', 'echo ${x:- # literal} # yes\n', (4, 22)),
    ('heredoc_continued', 'sh', 'cat <<EOF \\\n  <<BAR\n# first\nEOF\n# second\nBAR\n# yes\n', (4, 37)),
    ('py_prefixed', 'py', 'r"rawx"\nR"RAW"\nu"uni"\nU"UNI"\n', (25, 25)),
    ('js_hashbang', 'js', '#!/usr/bin/env node\nconst x=1; // yes\n', (23, 32)),
    ('jsx_text', 'jsx', 'const x=<div>hello // content</div>; // yes\n', (5, 38)),
    ('yaml_unicode_break', 'yaml', 'a: |\u2028  # data\n', (0, 8)),
    ('yaml_cr_comments', 'yaml', '# first\ra: 1 # yes\r', (10, 13)),
    ('yaml_key_colon', 'yaml', '"a:b": |\n  # data\n', (0, 12)),
    ('yaml_key_hash', 'yaml', "'a#b': >-\n  # data\n", (0, 13)),
    ('yaml_escaped_run', 'yaml', '"ru\\u006e": |\n  # yes\n', (4, 16)),
    ('yaml_single_run', 'yaml', "'run': |\n  # yes\n", (4, 11)),
    ('yaml_double_run', 'yaml', '"run": |\n  # yes\n', (4, 11)),
    ('yaml_anchor', 'yaml', 'a: &v "x # data" # yes\n', (4, 16)),
    ('yaml_tag', 'yaml', 'a: !!str "x # data" # yes\n', (4, 19)),
    ('yaml_custom_tag', 'yaml', 'a: !tag "x # data" # yes\n', (4, 18)),
    ('yaml_verbatim_tag', 'yaml', 'a: !<tag:example.org,2026:x> "x # data" # yes\n', (4, 39)),
    ('yaml_flow_properties', 'yaml', '{a: &v "x # data", b: *v} # yes\n', (4, 23)),
    ('yaml_block_properties', 'yaml', 'a: &v !!str |\n  # data\n', (0, 15)),
    ('yaml_run_properties', 'yaml', 'run: &v !!str |\n  cat <<EOF\n  # body\n  EOF\n  # yes\n', (4, 32)),
    ('yaml_plain_continuation', 'yaml', "a: text\n  'plain # real\nb: 1 # yes\n", (9, 24)),
    ('shell_quoted_shift', 'sh', 'echo "$((1 << true))"\n# real\ntrue\n# yes\n', (9, 31)),
    ('shell_arithmetic', 'sh', 'echo "$((1 << 2))"\n# yes\n', (4, 19)),
    ('shell_arithmetic_statement', 'sh', '((1 << 2))\n# yes\n', (4, 12)),
    ('shell_nested_arithmetic', 'sh', 'echo "$(( (1 + 1) << 2))"\n# yes\n', (4, 23)),
    ('yaml_run_arithmetic', 'yaml', 'run: |\n  echo "$((1 << 2))"\n  # yes\n', (4, 24)),
    ('shell_ansi_quote', 'sh', "echo $'it\\'s # data' # yes\n", (4, 21)),
    ('yaml_sequence_block', 'yaml', 'items:\n  - |\n    # data\n  - x # yes\n', (4, 19)),
    ('yaml_block_overlap', 'yaml', 'run:\n  - |\n    # a\n  - run: |\n      # bbbbbbbbbbbbbbbbbbbb\n', (21, 35)),
    ('shell_literal_keywords', 'sh', 'echo if case # yes\n', (4, 14)),
    ('shell_quoted_ansi_word', 'sh', 'cat <<"$\'EOF\'"\n# data\nEOF\n# more\n$\'EOF\'\n# yes\n', (4, 36)),
    ('shell_escaped_space', 'sh', 'echo \\ # data\n# yes\n', (4, 14)),
    ('shell_continued_word', 'sh', 'echo foo\\\n# data\n# yes\n', (4, 17)),
    ('shell_continued_hash_escape', 'sh', 'echo foo\\\n\\# data\n# yes\n', (4, 18)),
    ('shell_single_quote_continuation', 'sh', "echo 'a\\\n# b' # yes\n", (4, 14)),
    ('shell_escaped_backslash_newline', 'sh', 'echo foo\\\\\n# yes\n', (4, 13)),
    ('shell_continued_comment_bytes', 'sh', '# first\\\n second\necho x\n', (7, 18)),
    ('shell_comment_final_backslash', 'sh', '# yes\\', (5, 5)),
    ('shell_continued_scope_opener', 'sh', 'echo "$\\\n(echo x # real\n)"\n', (5, 20)),
    ('shell_continued_quoted_delimiter', 'sh', 'cat <<"EO\\\nF"\n# data\nEOF\n# yes\n', (4, 23)),
    ('shell_escaped_space_case_content', 'sh', 'echo $(printf \\ case) # yes\n', (4, 22)),
    ('shell_case_joined_backtick', 'sh', 'echo $(printf case`echo x`) # yes\n', (4, 28)),
    ('shell_quoted_case_delimiter', 'sh', "echo $(cat <<'case'\ncase\n) # yes\n", (4, 26)),
    ('shell_brace_in_word', 'sh', 'echo foo{#data}\n# yes\n', (4, 18)),
    ('shell_brace_word', 'sh', 'echo {# data}\n# yes\n', (4, 15)),
    ('shell_brace_group', 'sh', '{ echo x; } # yes\n', (4, 12)),
    ('shell_brace_after_expansion', 'sh', 'echo $x{#y}\n# yes\n', (4, 14)),
    ('shell_nonblank_whitespace', 'sh', 'echo a\r# b $c\xa0# d\n# yes\n', (4, 15)),
    ('shell_compound_assignment', 'sh', 'a=(1 # c\n)#x\nf() { :; } # yes\n', (6, 20)),
    ('shell_nested_compound_assignment', 'sh', 'echo $(a=(1)#x) # yes\n', (4, 18)),
    ('shell_process_substitution', 'sh', 'cat a<(echo a)#x >(:)#y\n# yes\n', (4, 24)),
    ('shell_bare_regex_word', 'sh', 'echo =~ # yes\n', (4, 10)),
    ('shell_escaped_regex_word', 'sh', 'echo \\=~ # yes\n', (4, 11)),
    ('shell_regex_after_condition', 'sh', '[[ a == a ]]; echo =~ # yes\n', (4, 19)),
    ('empty', 'py', ' \t\n', (0, 0)),
    ('js_literals', 'js', 'const a = "//"; const b = `// ${1}`; const c = /\\/\\//; // a\n/* b c */\n', (9, 51)),
    ('mjs', 'mjs', 'export const x = 1; // hi\n', (4, 19)),
    ('cjs', 'cjs', 'module.exports = 1; // hi\n', (4, 21)),
    ('cjs_top_level_return', 'cjs', '// c\nreturn f();\n', (3, 13)),
    ('jsx', 'jsx', 'const el = <div title="//">{/* hi */}text</div>; // é\n', (10, 46)),
    ('jsx_top_level_return', 'jsx', '// c\nreturn <div>// data</div>; /* d */\n', (8, 32)),
    ('yaml', 'yaml', 'a: "prefix # x"\nb: \'prefix # y\'\nc: a#b # yes\n# ok\n', (7, 36)),
    ('yaml_quotes', 'yml', 'a: "escaped \\" # still quoted" # é\nb: \'it\'\'s # quoted\' # z\n', (5, 46)),
    ('yaml_plain_quote', 'yaml', "name: it's plain # yes\n", (4, 18)),
    ('shell', 'sh', 'echo "# inside" \' # quote\' $# ${#x} a#b \\# # yes\n# ok\n', (7, 40)),
    ('heredoc', 'sh', 'cat <<\'EOF\' <<-"END" # hi\n# body\nEOF\n\t# body\n\tEND\n# ok\n', (6, 40)),
    ('shell_multiline', 'sh', 'echo "first\n# inside" # out\n# end\n', (8, 26)),
]


@pytest.mark.parametrize("case,suffix,source,want", CASES, ids=[c[0] for c in CASES])
def test_counts(case: str, suffix: str, source: str, want: tuple[int, int],
                tmp_path: Path) -> None:
    if suffix in ("js", "jsx", "mjs", "cjs") and not (ESPREE / "espree" / "package.json").is_file():
        pytest.skip("espree is not installed")
    path = tmp_path / f"sample.{suffix}"
    path.write_text(source, encoding="utf-8")
    language = counter.language_for(path)
    assert language is not None
    assert counter.measure_files(tmp_path, {path.name: language}) == {path.name: want}


JS_PARSE_CASES = [
    ('module', '// é😀\nexport const x = "//"; /* c */\n', (13, 31)),
    ('script', '// c\nwith (x) { f(); } /* d */\n', (8, 21)),
    ('commonjs', '// c\nwith (x) { return f(); } /* d */\n', (8, 27)),
    ('async_body', '// c\nreturn await f(); /* d */\n', (8, 23)),
]


@pytest.mark.parametrize('suffix', ['js', 'cjs', 'jsx'])
@pytest.mark.parametrize('case,source,want', JS_PARSE_CASES, ids=[c[0] for c in JS_PARSE_CASES])
def test_javascript_parse_forms_counts_and_cli(case, source, want, suffix, tmp_path):
    if not (ESPREE / 'espree' / 'package.json').is_file():
        pytest.skip('espree is not installed')
    name = f'{case}.{suffix}'
    (tmp_path / name).write_text(source, encoding='utf-8')
    assert counter.measure_files(tmp_path, {name: 'javascript'}) == {name: want}
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', name], check=True)
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                             '--report', '--root', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert f'{name}\t{want[0]}\t{want[1]}\t' in result.stdout


@pytest.mark.parametrize('name,source', [
    ('bad.js', 'const = 1;'),
    ('bad.cjs', 'const = 1;'),
    ('bad.jsx', 'const = 1;'),
    ('return.mjs', '// c\nreturn f();\n'),
    ('async_body.mjs', '// c\nreturn await f();\n'),
    ('script.mjs', '// c\nwith (x) { f(); }\n'),
])
def test_javascript_invalid_forms_fail_cli(name, source, tmp_path):
    if not (ESPREE / 'espree' / 'package.json').is_file():
        pytest.skip('espree is not installed')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / name).write_text(source, encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', name], check=True)
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                             '--report', '--root', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert name in result.stderr
    assert not result.stdout


def test_tracked_universe_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sources = {"a.py": "x = 1 # hi\n", "run": "#!/usr/bin/env -S bash -eu\n# hi\n",
               "a.yaml": "# hi\n", "no": "#!/usr/bin/env python3\n",
               "no.txt": "#!/bin/sh\n"}
    for name, source in sources.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", *sources], check=True)
    (tmp_path / "untracked.py").write_text("# hi\n", encoding="utf-8")
    assert counter.tracked_sources(tmp_path) == {"a.py": "python", "a.yaml": "yaml", "run": "shell"}
    assert counter.main(["--report", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "a.py\t3\t6\t50.000000%" in output
    assert "overall\t3\t32\t35\t91.428571%" in output
    assert "python\t1\t3\t6\t50.000000%" in output


REFUSED_SHELL = [
    'echo "$(( $(echo 1 # \'\n) + 1 ))"\n',
    'echo `printf case` # yes\n',
    'echo "$(ca\\\nse x in\nx) echo x ;; # real\nesac\n)" # yes\n',
    'echo $(printf \\case) # yes\n',
    'echo $(printf c\\ase) # yes\n',
    "cat <<'EO\\\nF'\n# data\nEOF\n# yes\n",
    'echo $(cat <<case\ncase\n) # yes\n',
    'echo "$(( $(cat <<EOF\n"2"\nEOF\n) + 1 ))"\n',
    'echo "$(( $(cat <<\'EOF\'\n2\nEOF\n) + 1 ))"\n',
    'cat <<"E OF"\n# data\nE OF\n',
    'echo "$(case x in\nx) echo x ;; # real\nesac\n)" # yes\n',
    'echo "$(! case x in\nx) echo x ;; # real\nesac\n)" # yes\n',
    'echo "$(case x in\n*/esac) echo x ;; # real\nesac\n)" # yes\n',
    "cat <<\\$'EOF'\n# data\n$EOF\n# yes\n",
    "cat <<$'EOF'\n# data\nEOF\n# yes\n",
    'echo "$(( \'x ))"\n',
    'echo "$(( "x ))"\n',
    '((1 + "2"))\n',
    "cat <<$'EOF'\n# data\nEOF\n",
    "cat <<$'it\\'s'\n# data\nit\\s\n# more\nit's\n# yes\n",
    "cat <<$'\\303\\251'\n# data\nÃ©\n# more\né\n# yes\n",
    "cat <<$'\\xC3\\xA9'\n# data\nÃ©\n# more\né\n# yes\n",
    "cat <<E'OF'\n# data\nEOF\n",
    'echo "$(case x in\nx) echo x ;;\nesac\n)"\n',
    'echo "$(time case x in\nx) echo x ;;\nesac\n)"\n',
    'echo "`case x in\nx) echo x ;;\nesac\n`"\n',
    '[[ ab =~ (a)#b ]] && echo y\n',
    '[[ ab == @(a)#b ]] && echo y\n',
    'cat <(case x in\nx) echo x ;; # real\nesac\n) # yes\n',
]
REFUSED_SOURCES = [("refused.yaml", source) for source in [
    '&k run: x\n*k: |\n  # yes\n',
    '&k run: |\n  # yes\n',
    '!!str run: |\n  # yes\n',
]] + [("refused.sh", source) for source in REFUSED_SHELL] + [
    ("refused.yaml", "run: |\n" + "".join("  " + line for line in source.splitlines(keepends=True)))
    for source in REFUSED_SHELL if '$((' in source or source.startswith('((')
]


OPEN_SCOPES = [
    'echo "$(\n',
    'echo ${\n',
    'echo ${x:-$(}\n',
    'echo ${x:-$((}\n',
    'echo ${x:-`}\n',
    'echo "`\n',
    'echo "$((\n',
    'echo "x\n',
    "echo 'x\n",
    "echo $'it\\'s\n",
    '((1 + 2)\n',
    '(echo x\n',
    'case x in @(x esac\n',
]
OPEN_SOURCES = [("open.sh", source) for source in OPEN_SCOPES] + [
    ("open.yaml", "run: |\n" + "".join("  " + line for line in source.splitlines(keepends=True))
     + "next: 1 # real\n") for source in OPEN_SCOPES
]


@pytest.mark.parametrize("name,source", [
    ("bad.py", "def (\n"), ("bad.py", "x = '\n"),
    ("bad.js", "const = 1;"), ("bad.jsx", "<div>"),
    ("bad.sh", "cat <<EOF\n# body\n"),
] + OPEN_SOURCES + REFUSED_SOURCES)
def test_unmeasurable_is_an_error(name: str, source: str, tmp_path: Path) -> None:
    if name.endswith((".js", ".jsx")) and not (ESPREE / "espree" / "package.json").is_file():
        pytest.skip("espree is not installed")
    (tmp_path / name).write_text(source, encoding="utf-8")
    language = counter.language_for(tmp_path / name)
    assert language is not None
    with pytest.raises(counter.MeasurementError, match=name):
        counter.measure_files(tmp_path, {name: language})


@pytest.mark.parametrize("dependency", ["node", "espree"])
def test_missing_dependency_fails_cli(dependency: str, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.js").write_text("// hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.js"], check=True)
    env = dict(os.environ)
    if dependency == "node":
        git = shutil.which("git")
        assert git
        (tmp_path / Path(git).name).symlink_to(git)
    env["PATH" if dependency == "node" else "COMMENT_RATCHET_ESPREE_DIR"] = str(tmp_path)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/comment_share.py"),
                             "--report", "--root", str(tmp_path)], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert dependency in result.stderr.lower()
    assert not result.stdout


@pytest.mark.parametrize("name,source", OPEN_SOURCES + REFUSED_SOURCES)
def test_unfinished_scopes_cli(name: str, source: str, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", name], check=True)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ci/comment_share.py"),
                             "--report", "--root", str(tmp_path)],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert name in result.stderr
    assert not result.stdout


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX bash runtime evidence; Windows uses the WSL stub')
@pytest.mark.parametrize('source,stdout,comments', [
    ('# first\\\nprintf second\n', 'second', 7),
    ('echo ${x:-$(echo x # real\n)}\n', 'x\n', 5),
    ('echo ${x:-`echo x # real\n`}\n', 'x\n', 5),
])
def test_shell_comments_match_execution(source, stdout, comments):
    result = subprocess.run(['bash', '-c', source], capture_output=True, text=True, check=True)
    assert result.stdout == stdout
    assert not result.stderr
    assert counter.quoted_comments(source, shell=True) == comments


def test_unresolved_node_names_missing_executable(tmp_path: Path, monkeypatch):
    (tmp_path / 'a.js').write_text('// hi\n', encoding='utf-8')
    monkeypatch.setattr(shutil, 'which', lambda _executable: None)
    with pytest.raises(counter.MeasurementError, match='node'):
        counter.javascript_comments(tmp_path, ['a.js'])


@pytest.mark.parametrize('name', ['bad.py', 'bad.js', 'bad.yaml', 'bad.sh', 'shell',
                                  'bad.ts', 'bad.tsx', 'bad.mts', 'bad.cts', 'bad.sql'])
def test_unreadable_tracked_source_names_file(tmp_path, name):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path / name
    path.write_bytes(b'#!/bin/sh\n# hi\n' if name == 'shell' else b'# hi\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', name], check=True)
    path.unlink()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/comment_share.py'),
                             '--root', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert name in result.stderr


@pytest.mark.parametrize('suffix', ['py', 'js', 'yaml', 'sh'])
def test_invalid_utf8_names_file(tmp_path, suffix):
    name = f'bad.{suffix}'
    (tmp_path / name).write_bytes(b'# \xff\n')
    with pytest.raises(counter.MeasurementError, match=name):
        counter.measure_files(tmp_path, {name: counter.language_for(tmp_path / name)})


def test_js_token_helper_preserves_semantic_line_breaks(tmp_path):
    helper = ROOT / 'scripts/ci/js_comment_tokens.mjs'
    source = tmp_path / 'tokens.js'
    def tokens(text):
        source.write_text(text)
        result = subprocess.run(['node', str(helper), str(ESPREE), str(source), 'plain'],
                                capture_output=True, text=True, check=True)
        import json
        return json.loads(result.stdout)
    original = tokens('function f() { return 1; }')
    assert tokens('function f() { /* comment */ return 1; }') == original
    assert tokens('function f() { return\n1; }') != original


TS_CASES = [
    ('line', '// c\nlet x=1;\n', (3, 10)),
    ('block', '/* c */let x=1;\n', (5, 12)),
    ('doc', '/** c */let x=1;\n', (6, 13)),
    ('adjacent', '/**a*//*b*///c\nlet x=1;/*d*/', (19, 26)),
    ('single_string', "let x='// /*'; // c\n", (3, 15)),
    ('double_string', 'let x="// /*"; // c\n', (3, 15)),
    ('template', 'let x=`// /*`; // c\n', (3, 15)),
    ('template_expression', 'let x=`/*${1/*c*/} //`; // d\n', (8, 24)),
    ('nested_template', 'let x=`a${`b${1/*c*/}/*`} //`; // d\n', (8, 31)),
    ('template_middle', 'let x=`a${1}/*${2/*c*/}//`; // d\n', (8, 29)),
    ('leading_literal_comment', 'let x=/*c*/"//"; // d\n', (8, 18)),
    ('regex', r'let x=/[/*]\/\//; // c' + '\n', (3, 19)),
    ('division', 'let x=4/2; /* c */\n', (5, 14)),
    ('hashbang', '#!/usr/bin/env node\nlet x=1;\n', (18, 25)),
    ('utf8', '// é😀\nlet x="ø";\n', (8, 18)),
    ('unicode_space', '// a\u0085b\u2003c\nlet x=1;\n', (5, 12)),
    ('empty', '', (0, 0)),
    ('whitespace', ' \t\n\u2003', (0, 0)),
]


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts'])
@pytest.mark.parametrize('case,source,want', TS_CASES, ids=[c[0] for c in TS_CASES])
def test_typescript_counts(suffix, case, source, want, tmp_path):
    path = tmp_path / f'sample.{suffix}'
    path.write_text(source, encoding='utf-8')
    assert counter.language_for(path) == 'typescript'
    measured = counter.measure_files(tmp_path, {path.name: 'typescript'})
    assert measured == {path.name: want}
    if want == (0, 0):
        assert counter.share(measured[path.name]) == 0


TSX_CASES = [
    ('line_text', 'const x=<div>// text</div>;', (0, 25)),
    ('block_text', 'const x=<div>/* text */</div>;', (0, 27)),
    ('multiline_text', 'const x=<div>\n// text\n/* text */\n</div>;', (0, 33)),
    ('expression_comment', 'const x=<div>{/* c */}</div>;', (5, 26)),
    ('attribute', 'const x=<div title="// /*"/>; // c\n', (3, 29)),
    ('fragment', 'const x=<>// text{/* c */}<b>/*text*/</b></>; // d\n', (8, 44)),
    ('white_text', 'const x=<div> \n <b/> \t </div>; // c\n', (3, 26)),
]


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts'])
@pytest.mark.parametrize('case,source,want', TSX_CASES, ids=[c[0] for c in TSX_CASES])
def test_tsx_counts(suffix, case, source, want, tmp_path):
    path = tmp_path / f'sample.{suffix}'
    path.write_text(source, encoding='utf-8')
    if suffix == 'tsx':
        assert counter.measure_files(tmp_path, {path.name: 'typescript'}) == {path.name: want}
    else:
        with pytest.raises(counter.MeasurementError, match=path.name):
            counter.measure_files(tmp_path, {path.name: 'typescript'})


SQL_CASES = [
    ('line', '-- c\nselect 1;\n', (3, 11)),
    ('cr_line', '-- c\rselect 1;\r', (3, 11)),
    ('eof_line', 'select 1;-- c', (3, 11)),
    ('block', '/* c */select 1;', (5, 13)),
    ('nested', '/* a /* b */ c */select 1;', (11, 19)),
    ('adjacent', '/*a*//*b*/--c\nselect 1;', (13, 21)),
    ('single', "select '-- /*';-- c\n", (3, 16)),
    ('single_double', "select 'a''-- /*';-- c\n", (3, 19)),
    ('identifier', 'select "-- /*";-- c\n', (3, 16)),
    ('identifier_double', 'select "a""-- /*";-- c\n', (3, 19)),
    ('plain_backslash', "select 'a\\';-- c\n", (3, 14)),
    ('escape_upper', "select E'a\\'-- /*';-- c\n", (3, 20)),
    ('escape_lower', "select e'a\\'-- /*';-- c\n", (3, 20)),
    ('escape_backslash', "select E'a\\\\';-- c\n", (3, 16)),
    ('escape_doubled', "select E'a''-- /*';-- c\n", (3, 20)),
    ('escape_doubled_then_backslash', "select E'a''b\\'--c';-- d\n", (3, 22)),
    ('embedded_e', "select nameE'a\\';-- c\n", (3, 19)),
    ('dollar_empty', 'select $$-- /*$$;-- c\n', (3, 18)),
    ('dollar_tag', 'select $tag$-- /*$tag$;-- c\n', (3, 24)),
    ('dollar_case', 'select $tag$--$TAG$/*$tag$;-- c\n', (3, 29)),
    ('dollar_tag_chars', 'select $_a9$--$_a9$;-- c\n', (3, 22)),
    ('dollar_tag_unicode', 'select $é$--$é$;-- c\n', (3, 20)),
    ('parameter', 'select $1;-- c\n', (3, 12)),
    ('numeric_tag_nearmiss', 'select $1$-- c\n', (3, 12)),
    ('tag_punctuation_nearmiss', 'select $ta-g$-- c\n', (3, 15)),
    ('identifier_dollar', 'select a$tag$-- c\n', (3, 15)),
    ('identifier_double_dollar', 'select a$$-- c\n', (3, 12)),
    ('identifier_unicode_dollar', 'select é$tag$-- c\n', (3, 16)),
    ('separated_dollar', 'select a $tag$--$tag$;-- c\n', (3, 23)),
    ('utf8', '-- é😀\nselect 1;\n', (8, 16)),
    ('unicode_space', '-- a\u0085b\u2003c\nselect 1;\n', (5, 13)),
    ('empty', '', (0, 0)),
    ('whitespace', ' \t\n\u2003', (0, 0)),
]


@pytest.mark.parametrize('case,source,want', SQL_CASES, ids=[c[0] for c in SQL_CASES])
def test_sql_counts(case, source, want, tmp_path):
    path = tmp_path / 'sample.sql'
    path.write_text(source, encoding='utf-8')
    assert counter.language_for(path) == 'sql'
    measured = counter.measure_files(tmp_path, {path.name: 'sql'})
    assert measured == {path.name: want}
    if want == (0, 0):
        assert counter.share(measured[path.name]) == 0


SQL_CONTINUATION_CASES = [
    ('line_text', "SELECT E'a'\n'\\'--inside' AS x; --real\n", (6, 32)),
    ('block_text', "SELECT E'a'\n'\\'/*inside*/' AS x; --real\n", (6, 34)),
    ('line_gap', "SELECT E'a' --gap\n'\\'--inside' AS x; --real\n", (11, 37)),
    ('line_gap_cr', "SELECT E'a' --gap\r'\\'--inside' AS x; --real\r", (11, 37)),
    ('line_gaps', "SELECT E'a'\n--one\n--two\n'\\'--inside' AS x; --real\n", (16, 42)),
    ('block_gap', "SELECT E'a' /*gap*/\n'\\'; --real\n", (13, 27)),
    ('block_gap_newline', "SELECT E'a'\n/*gap*/'\\'; --real\n", (13, 27)),
    ('cr', "SELECT E'a'\r'\\'--inside' AS x; --real\r", (6, 32)),
    ('crlf', "SELECT E'a'\r\n'\\'--inside' AS x; --real\r\n", (6, 32)),
    ('mixed_space', "SELECT E'a' \t\f\v\n \t\f\v'\\'--inside' AS x; --real\n", (6, 32)),
    ('doubled', "SELECT E'a'\n'b''\\'--inside' AS x; --real\n", (6, 35)),
    ('escape_then_doubled', "SELECT E'a'\n'\\'''--inside' AS x; --real\n", (6, 34)),
    ('chain', "SELECT E'a'\n'b'\n'\\'--inside' AS x; --real\n", (6, 35)),
    ('same_line_after_continuation', "SELECT E'a'\n'b' '\\'; --real\n", (6, 23)),
    ('no_newline', "SELECT E'a' '\\'; --real\n", (6, 20)),
    ('tab_only', "SELECT E'a'\t'\\'; --real\n", (6, 20)),
    ('formfeed_only', "SELECT E'a'\f'\\'; --real\n", (6, 20)),
    ('vertical_tab_only', "SELECT E'a'\v'\\'; --real\n", (6, 20)),
    ('operator', "SELECT E'a'\n||'\\'; --real\n", (6, 22)),
    ('unicode_gap', "SELECT E'a'\u0085\n'\\'; --real\n", (6, 20)),
    ('identifier_quote', 'SELECT E\'a\'\n"x\\"; --real\n', (6, 21)),
    ('reset_after_string', "SELECT E'a'\n'b'; SELECT '\\'; --real\n", (6, 30)),
    ('eof_after_gap', "SELECT E'a'\n \t", (0, 10)),
]


@pytest.mark.parametrize('prefix', ['E', 'e'])
@pytest.mark.parametrize('case,source,want', SQL_CONTINUATION_CASES,
                         ids=[c[0] for c in SQL_CONTINUATION_CASES])
def test_sql_escape_continuation_counts(prefix, case, source, want, tmp_path):
    source = source.replace("E'", prefix + "'", 1)
    path = tmp_path / 'sample.sql'
    path.write_text(source, encoding='utf-8')
    assert counter.measure_files(tmp_path, {path.name: 'sql'}) == {path.name: want}


@pytest.mark.parametrize('source,want', [
    ("SELECT 'a'\n'\\'; --real\n", (6, 19)),
    ("SELECT 'a'\n'b''\\'; --real\n", (6, 22)),
])
def test_sql_plain_continuation_keeps_backslash_literal(source, want, tmp_path):
    path = tmp_path / 'plain.sql'
    path.write_text(source, encoding='utf-8')
    assert counter.measure_files(tmp_path, {path.name: 'sql'}) == {path.name: want}


BAD_TS = [
    'const = 1;', 'let x="open', '/* open', 'let x=`open',
    'let x=/[/', 'function f( {',
]
BAD_SQL = [
    "select 'open", 'select "open', 'select $$open', 'select $tag$open',
    'select $tag$open$TAG$', '/* open', '/* outer /* inner */', '/* outer /* inner',
    "select 'a\\'-- c\n'",
    "select E'a\\'", "select e'a\\'",
    "SELECT E'a'\n'\\'", "SELECT e'a'\n'\\'",
    "SELECT E'a'\n'\\'; --real\n", "SELECT e'a'\n'\\'; --real\n",
    "SELECT E'a' --gap\n'\\'", "SELECT e'a' --gap\n'\\'",
]


@pytest.mark.parametrize('name,source', [
    (f'bad.{ext}', source) for ext in ['ts', 'tsx', 'mts', 'cts'] for source in BAD_TS
] + [(f'bad.{ext}', 'const x=<div/>;') for ext in ['ts', 'mts', 'cts']]
  + [('bad.tsx', 'const x=<div>')]
  + [('bad.tsx', 'const x=<number>1;')]
  + [(f'bad.{ext}', '﻿#!/usr/bin/env node\n// c\nlet x=1;\n')
     for ext in ['ts', 'tsx', 'mts', 'cts']]
  + [('bad.sql', source) for source in BAD_SQL])
def test_typescript_sql_failure_names_file(name, source, tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / name).write_text(source)
    subprocess.run(['git', '-C', str(tmp_path), 'add', name], check=True)
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                             '--report', '--root', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert name in result.stderr
    assert not result.stdout


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts'])
def test_backslash_named_malformed_source_fails(suffix, tmp_path):
    # The compiler normalizes path separators, so a tracked POSIX filename
    # carrying a backslash would skip the parse-diagnostic gate entirely
    # unless every file is verified present in the program.
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    name = f'weird\\bad.{suffix}'
    (tmp_path / name).write_text('const x = ;\n')
    sibling = tmp_path / f'weird/bad.{suffix}'
    sibling.parent.mkdir()
    sibling.write_text('const ok = 1;\n')
    subprocess.run(['git', '-C', str(tmp_path), 'add', name, str(sibling)], check=True)
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                             '--report', '--root', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert name in result.stderr
    assert not result.stdout


def test_typescript_sql_report_reproduction(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    sources = {'a.ts': '// c\nexport const x = 1;\n',
               'b.sql': '-- c\nselect 1;\n', 'c.py': '# c\nx=1\n',
               'types.d.ts': '/** c */declare const x: number;\n'}
    for name, source in sources.items():
        (tmp_path / name).write_text(source)
    subprocess.run(['git', '-C', str(tmp_path), 'add', *sources], check=True)
    (tmp_path / 'untracked.ts').write_text('// ignored\n')
    (tmp_path / 'untracked.sql').write_text('-- ignored\n')
    assert counter.tracked_sources(tmp_path) == {
        'a.ts': 'typescript', 'b.sql': 'sql', 'c.py': 'python', 'types.d.ts': 'typescript'}
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/ci/comment_share.py'),
                             '--report', '--root', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for row in ['a.ts\t3\t18\t16.666667%', 'b.sql\t3\t11\t27.272727%',
                'c.py\t2\t5\t40.000000%', 'types.d.ts\t6\t27\t22.222222%']:
        assert row in result.stdout
    assert 'untracked.' not in result.stdout


@pytest.mark.parametrize('suffix', ['ts', 'tsx', 'mts', 'cts', 'sql'])
def test_typescript_sql_invalid_utf8_names_file(suffix, tmp_path):
    name = f'bad.{suffix}'
    (tmp_path / name).write_bytes(b'// \xff\n')
    with pytest.raises(counter.MeasurementError, match=name):
        counter.measure_files(tmp_path, {name: counter.language_for(tmp_path / name)})
