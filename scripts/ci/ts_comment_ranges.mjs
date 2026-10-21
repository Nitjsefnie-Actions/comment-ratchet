import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

try {
  const require = createRequire(import.meta.url);
  const ast = require('typescript/ast');
  const sync = require('typescript/sync');
  const files = JSON.parse(readFileSync(0, 'utf8'));
  const api = new sync.API();
  // One program over every file keeps the parse-diagnostic gate fail-closed
  // for .d.ts too, which the transpile path cannot carry.
  const program = api.createProgram(files, { noResolve: true, noLib: true });
  const allDiagnostics = program.getSyntacticDiagnostics();
  const comments = files.map((file) => {
    try {
      const source = readFileSync(file, 'utf8');
      const diagnostics = allDiagnostics.filter((d) => d.fileName === file);
      if (diagnostics.length) {
        throw new Error(diagnostics.map((d) => d.text).join('\n'));
      }
      const kind = file.endsWith('.tsx') ? ast.ScriptKind.TSX : ast.ScriptKind.TS;
      const parsed = api.createSourceFile(file, source, { scriptKind: kind }).sourceFile;
      const spans = [];
      function visit(node) {
        switch (node.kind) {
          case ast.SyntaxKind.StringLiteral:
          case ast.SyntaxKind.RegularExpressionLiteral:
          case ast.SyntaxKind.NoSubstitutionTemplateLiteral:
          case ast.SyntaxKind.TemplateHead:
          case ast.SyntaxKind.TemplateMiddle:
          case ast.SyntaxKind.TemplateTail:
          case ast.SyntaxKind.JsxText:
            spans.push([node.kind === ast.SyntaxKind.JsxText ? node.pos : node.getStart(), node.end]);
            return;
          default:
            node.forEachChild(visit);
        }
      }
      visit(parsed);
      spans.sort((left, right) => left[0] - right[0]);
      let end = 0;
      const parts = [];
      for (const [start, finish] of spans) {
        parts.push(source.slice(end, start), source.slice(start, finish).replace(/[^\r\n\u2028\u2029]/g, ' '));
        end = finish;
      }
      parts.push(source.slice(end));
      const scanner = ast.createScanner(false, ast.LanguageVariant.Standard, parts.join(''));
      const ranges = [];
      for (let token = scanner.scan(); token !== ast.SyntaxKind.EndOfFile; token = scanner.scan()) {
        // 7.1 scans a shebang as Unknown where 5.9.3 emitted ShebangTrivia.
        const isShebang = token === ast.SyntaxKind.Unknown
          && scanner.getTokenText().startsWith('#!');
        if (isShebang
            || token === ast.SyntaxKind.SingleLineCommentTrivia
            || token === ast.SyntaxKind.MultiLineCommentTrivia) {
          ranges.push(source.slice(scanner.getTokenStart(), scanner.getTokenEnd()));
        }
      }
      return ranges;
    } catch (error) {
      throw new Error(`${file}: ${error.message}`);
    }
  });
  process.stdout.write(JSON.stringify(comments));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
