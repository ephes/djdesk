const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { test } = require('node:test');
const { buildFixture } = require('./build-fixture.cjs');

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
        assert.equal(file, archive);
        if (fail === 'extract' || fail === 'cleanup') throw new Error('synthetic extract');
        await fs.mkdir(path.join(scratch, 'python'));
      },
    }), new RegExp(`synthetic ${fail === 'cleanup' ? 'extract' : fail}`));
    assert.deepEqual((await fs.readdir(root)).sort(), ['archive', 'electron', 'unrelated']);
    assert.equal(allocations, fail === 'checksum' ? 0 : 1);
    assert.equal(await fs.readFile(archive, 'utf8'), 'cached archive');
    assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
  });
}
