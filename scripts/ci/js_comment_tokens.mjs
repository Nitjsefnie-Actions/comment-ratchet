import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const espreeDir = process.argv[2];
const sourceFile = process.argv[3];
const jsx = process.argv[4] === 'jsx';

try {
  const require = createRequire(import.meta.url);
  const espree = require(path.join(espreeDir, 'espree'));
  const src = readFileSync(sourceFile, 'utf8');
  const tokens = espree.tokenize(src, {
    ecmaVersion: 'latest',
    sourceType: 'module',
    comment: false,
    loc: true,
    ecmaFeatures: { jsx },
  });
  process.stdout.write(JSON.stringify(tokens.map((t, i, a) => [
    t.type, t.value, i > 0 && t.loc.start.line > a[i - 1].loc.end.line,
  ])));
} catch (err) {
  process.stderr.write(String((err && err.message) || err));
  process.exit(1);
}
