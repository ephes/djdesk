const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { Readable } = require('node:stream');
const { test } = require('node:test');
const { checksum, downloadPython, snapshotPythonArchive } = require('../python-download');

const content = Buffer.from('invented cached archive');
const sha = createHash('sha256').update(content).digest('hex');
const noNetwork = () => { throw new Error('must not access network'); };

async function fixture(t) {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'djdesk-cache-test-'));
  t.after(() => fsp.rm(root, { recursive: true, force: true }));
  return { root, file: path.join(root, 'archive') };
}

for (const operation of ['checksum', 'snapshot']) {
  test(`${operation} rejects a short archive read despite stable metadata`, async (t) => {
    const { root, file } = await fixture(t);
    await fsp.writeFile(file, content);
    const short = content.subarray(0, -1);
    const snapshot = path.join(root, 'snapshot');
    const limits = { maxBytes: content.length, signal: new AbortController().signal };
    const read = () => operation === 'checksum'
      ? checksum(file, createHash('sha256').update(short).digest('hex'), limits)
      : snapshotPythonArchive(file, snapshot, limits);

    const open = fsp.open;
    t.mock.method(fsp, 'open', async (...args) => {
      const handle = await open(...args);
      handle.createReadStream = () => Readable.from([short]);
      return handle;
    });
    await assert.rejects(read(), /Python cache read length mismatch/);
  });
}

test('a complete archive can be hashed and copied at the byte ceiling', async (t) => {
  const { root, file } = await fixture(t);
  await fsp.writeFile(file, content);
  const snapshot = path.join(root, 'snapshot');
  const limits = { maxBytes: content.length, signal: new AbortController().signal };
  assert.equal(await checksum(file, sha, limits), true);
  await snapshotPythonArchive(file, snapshot, limits);
  assert.deepEqual(await fsp.readFile(snapshot), content);
});

test('oversized cache is refused before hashing or network access', async (t) => {
  const { file } = await fixture(t);
  await fsp.writeFile(file, content);
  await assert.rejects(downloadPython('https://fixture.invalid/', file, sha,
    { get: noNetwork, maxBytes: content.length - 1 }), /byte limit/);
  assert.deepEqual(await fsp.readFile(file), content);
});

test('cache symlink is refused without touching its target', async (t) => {
  const { root, file } = await fixture(t);
  const target = path.join(root, 'target');
  await fsp.writeFile(target, content);
  await fsp.symlink(target, file);
  await assert.rejects(downloadPython('https://fixture.invalid/', file, sha,
    { get: noNetwork }), /regular|symbolic|ELOOP/);
  assert.ok((await fsp.lstat(file)).isSymbolicLink());
  assert.deepEqual(await fsp.readFile(target), content);
});

test('FIFO cache admission cannot block on open', { skip: process.platform === 'win32' }, async (t) => {
  const { file } = await fixture(t);
  assert.equal(spawnSync('mkfifo', [file]).status, 0);
  const result = spawnSync(process.execPath, ['-e', `
    require(process.argv[1]).downloadPython('https://fixture.invalid/', process.argv[2], process.argv[3])
      .then(() => process.exitCode = 2, error => {
        if (!/regular/.test(error.message)) throw error;
      });
  `, path.resolve(__dirname, '../python-download.js'), file, sha], { timeout: 2000, encoding: 'utf8' });
  assert.equal(result.status, 0, `${result.error || ''} ${result.stderr}`);
  assert.ok((await fsp.lstat(file)).isFIFO());
});

test('replacement during hashing cannot admit different cache bytes', async (t) => {
  const { file } = await fixture(t);
  await fsp.writeFile(file, content);
  const original = fs.createReadStream;
  const originalOpen = fsp.open;
  // Exercise both the old path stream and the descriptor-bound reader.
  fs.createReadStream = (...args) => {
    const stream = original(...args);
    stream.once('data', () => {
      fs.renameSync(file, `${file}.old`);
      fs.writeFileSync(file, 'replacement');
    });
    return stream;
  };
  fsp.open = async (...args) => {
    const handle = await originalOpen(...args);
    const read = handle.createReadStream.bind(handle);
    handle.createReadStream = (...options) => {
      const stream = read(...options);
      stream.once('data', () => {
        fs.renameSync(file, `${file}.old`);
        fs.writeFileSync(file, 'replacement');
      });
      return stream;
    };
    return handle;
  };
  t.after(() => { fs.createReadStream = original; fsp.open = originalOpen; });
  await assert.rejects(downloadPython('https://fixture.invalid/', file, sha,
    { get: noNetwork }), /changed/);
  assert.equal(await fsp.readFile(file, 'utf8'), 'replacement');
});

test('cache hashing is inside the total deadline and closes its descriptor', async (t) => {
  const { PassThrough } = require('node:stream');
  const { file } = await fixture(t);
  await fsp.writeFile(file, content);
  const originalOpen = fsp.open;
  let handle;
  fsp.open = async (...args) => {
    handle = await originalOpen(...args);
    handle.createReadStream = ({ signal }) => {
      const stream = new PassThrough();
      signal.addEventListener('abort', () => stream.destroy(signal.reason), { once: true });
      return stream;
    };
    return handle;
  };
  t.after(() => { fsp.open = originalOpen; });
  await assert.rejects(downloadPython('https://fixture.invalid/', file, sha,
    { get: noNetwork, timeoutMs: 20 }), /deadline/);
  assert.equal(handle.fd, -1);
});

test('cache growth during hashing also obeys the byte ceiling', async (t) => {
  const { file } = await fixture(t);
  await fsp.writeFile(file, content);
  const originalOpen = fsp.open;
  fsp.open = async (...args) => {
    const handle = await originalOpen(...args);
    const originalStream = handle.createReadStream.bind(handle);
    handle.createReadStream = (options) => {
      const stream = originalStream({ ...options, highWaterMark: 1 });
      stream.once('data', () => fs.appendFileSync(file, 'extra'));
      return stream;
    };
    return handle;
  };
  t.after(() => { fsp.open = originalOpen; });
  await assert.rejects(downloadPython('https://fixture.invalid/', file, sha,
    { get: noNetwork, maxBytes: content.length }), /byte limit/);
});
