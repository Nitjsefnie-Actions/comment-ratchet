import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

try {
  const require = createRequire(import.meta.url);
  const espree = require(path.join(process.argv[2], 'espree'));
  const files = JSON.parse(readFileSync(0, 'utf8'));
  const comments = files.map((file) => {
    try {
      const source = readFileSync(file, 'utf8');
      const forms = [{ sourceType: 'module' }];
      if (!file.endsWith('.mjs')) {
        forms.push(
          { sourceType: 'script' },
          { sourceType: 'commonjs' },
          // Evaluator bodies can combine top-level return and await.
          { sourceType: 'module', ecmaFeatures: { globalReturn: true } },
        );
      }
      for (const [index, form] of forms.entries()) {
        try {
          const parsed = espree.parse(source, {
            ...form,
            ecmaVersion: 'latest',
            comment: true,
            range: true,
            ecmaFeatures: { jsx: file.endsWith('.jsx'), ...form.ecmaFeatures },
          });
          return parsed.comments.map(({ range }) => source.slice(...range));
        } catch (error) {
          if (index === forms.length - 1) throw error;
        }
      }
    } catch (error) {
      throw new Error(`${file}: ${error.message}`);
    }
  });
  process.stdout.write(JSON.stringify(comments));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
