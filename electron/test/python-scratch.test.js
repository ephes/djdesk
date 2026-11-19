const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { test } = require('node:test');
const { createHash } = require('node:crypto');
const { buildFixture } = require('./build-fixture.cjs');
const { checksum } = require('../python-download');

test('extracts the verified archive even if the cache is replaced after verification', async (t) => {
  const { root, bundle, build } = buildFixture(t);
  const archive = path.join(root, 'archive');
  const replacement = path.join(root, 'incoming');
  const original = 'verified fixture archive';
  const expected = createHash('sha256').update(original).digest('hex');
  await fs.writeFile(archive, original);
  await fs.writeFile(replacement, 'replacement cache');
  const io = { ...fs, mkdtemp: () => fs.mkdtemp(path.join(root, 'scratch-')) };
  await build.extractPython(archive, 'fixture', path.join(bundle, 'python'), {
    io,
    verify: async (file) => {
      assert.ok(await checksum(file, expected, { maxBytes: 1024, signal: AbortSignal.timeout(5000) }));
      await fs.rename(replacement, archive);
    },
    extract: async (file, scratch) => {
      assert.equal(await fs.readFile(file, 'utf8'), original);
      await fs.mkdir(path.join(scratch, 'python'), { recursive: true });
    },
  });
  assert.equal(await fs.readFile(archive, 'utf8'), 'replacement cache');
  assert.deepEqual((await fs.readdir(root)).sort(), ['archive', 'electron']);
  assert.ok((await fs.stat(path.join(bundle, 'python'))).isDirectory());
});

for (const fail of ['checksum', 'extract', 'copy', 'cleanup']) {
  test(`${fail} failure owns scratch cleanup and preserves the primary cause`, async (t) => {
    const { root, bundle, build } = buildFixture(t);
    const archive = path.join(root, 'archive');
    const unrelated = path.join(root, 'unrelated');
    await fs.writeFile(archive, 'cached archive');
    await fs.mkdir(unrelated);
    await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    let allocations = 0;
    const io = { ...fs,
      async mkdtemp() { allocations++; return fs.mkdtemp(path.join(root, 'scratch-')); },
      async rename() { throw Object.assign(new Error('cross device'), { code: 'EXDEV' }); },
      async cp() { throw new Error('synthetic copy'); },
      async rm(file, options) {
        await fs.rm(file, options);
        if (fail === 'cleanup' && path.basename(file).startsWith('scratch-')) {
          throw new Error('synthetic cleanup');
        }
      },
    };
    await assert.rejects(build.extractPython(archive, 'fixture', path.join(bundle, 'python'), {
      io,
      verify: async () => { if (fail === 'checksum') throw new Error('synthetic checksum'); },
      extract: async (file, scratch) => {
        assert.notEqual(file, archive);
        assert.equal(await fs.readFile(file, 'utf8'), 'cached archive');
        if (fail === 'extract' || fail === 'cleanup') throw new Error('synthetic extract');
        await fs.mkdir(path.join(scratch, 'python'), { recursive: true });
      },
    }), new RegExp(`synthetic ${fail === 'cleanup' ? 'extract' : fail}`));
    assert.deepEqual((await fs.readdir(root)).sort(), ['archive', 'electron', 'unrelated']);
    assert.equal(allocations, 1);
    assert.equal(await fs.readFile(archive, 'utf8'), 'cached archive');
    assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
  });
}
