import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

try {
  const require = createRequire(import.meta.url);
  const ts = require('../../node_modules/typescript');
  const files = JSON.parse(readFileSync(0, 'utf8'));
  const comments = files.map((file) => {
    try {
      const source = readFileSync(file, 'utf8');
      const kind = file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
      const parsed = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
      if (parsed.parseDiagnostics.length) {
        throw new Error(parsed.parseDiagnostics.map((diagnostic) =>
          ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')).join('\n'));
      }
      const spans = [];
      function visit(node) {
        switch (node.kind) {
          case ts.SyntaxKind.StringLiteral:
          case ts.SyntaxKind.RegularExpressionLiteral:
          case ts.SyntaxKind.NoSubstitutionTemplateLiteral:
          case ts.SyntaxKind.TemplateHead:
          case ts.SyntaxKind.TemplateMiddle:
          case ts.SyntaxKind.TemplateTail:
          case ts.SyntaxKind.JsxText:
            // JSX text has no leading trivia: // and /* belong to the text.
            spans.push([node.kind === ts.SyntaxKind.JsxText ? node.pos : node.getStart(parsed), node.end]);
            return;
          default:
            ts.forEachChild(node, visit);
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
      // Mask opaque syntax at the same UTF-16 offsets so the scanner never
      // mistakes literal contents for trivia, including regex and JSX text.
      const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, parts.join(''));
      const ranges = [];
      for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
        if (token === ts.SyntaxKind.SingleLineCommentTrivia
            || token === ts.SyntaxKind.MultiLineCommentTrivia
            || token === ts.SyntaxKind.ShebangTrivia) {
          ranges.push(source.slice(scanner.getTokenPos(), scanner.getTextPos()));
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
