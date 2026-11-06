const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

test('locked Electron, builder and TOML meet refreshed security floors', () => {
  const lock = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package-lock.json')));
  for (const [name, floor] of [['electron', [45, 2, 3]], ['electron-builder', [26, 18, 0]],
    ['toml', [5, 0, 1]]]) {
    const actual = lock.packages[`node_modules/${name}`].version.split('.').map(Number);
    const comparison = actual.findIndex((value, index) => value !== floor[index]);
    assert.ok(comparison === -1 || actual[comparison] > floor[comparison], name);
  }
});

test('bundle preparation uses registered checksums before cache publication', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'build-django.js'), 'utf8');
  assert.match(source, /await downloadPython\(downloadUrl, archivePath, PYTHON_SHA256\[assetName\]\)/);
  assert.doesNotMatch(source, /response\.pipe\(file\)/);
});
