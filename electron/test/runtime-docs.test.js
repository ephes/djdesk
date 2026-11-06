const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

test('runtime prerequisites agree with package engines and the standalone bundle', () => {
  const root = path.join(__dirname, '..', '..');
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'electron', 'package.json'), 'utf8'));
  for (const file of ['README.md', 'electron/README.md', 'docs/electron.rst']) {
    const doc = fs.readFileSync(path.join(root, file), 'utf8');
    assert.ok(doc.includes(`Node.js ${pkg.engines.node}`), file);
    assert.match(doc, /standalone/);
    assert.doesNotMatch(doc, /Phase 2 — Option B|Node\.js v18\+|Phase 3 .*will build/);
  }
  assert.equal(pkg.scripts.bundle, 'node build-django.js');
  assert.equal(pkg.scripts.start, 'electron .');
});
