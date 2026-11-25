const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const { test } = require('node:test');
const { downloadPython, checksum } = require('../python-download');

const content = Buffer.from('synthetic interpreter archive');
const sha = createHash('sha256').update(content).digest('hex');

function fixture(steps) {
  let calls = 0;
  const get = (url, { signal }, callback) => {
    calls++;
    const request = new EventEmitter();
    const response = new PassThrough();
    const step = steps[Math.min(calls - 1, steps.length - 1)];
    response.statusCode = step.status || 200;
    response.headers = step.headers || {};
    const abort = () => {
      request.emit('error', new Error('aborted'));
      response.destroy(new Error('aborted'));
    };
    signal.addEventListener('abort', abort, { once: true });
    response.on('close', () => signal.removeEventListener('abort', abort));
    queueMicrotask(() => {
      callback(response);
      // Let the caller attach its pipeline before emitting fixture errors.
      setImmediate(() => {
        if (step.error) response.destroy(new Error('truncated response'));
        else if (!step.stall) response.end(step.body || content);
      });
    });
    return request;
  };
  return { get, calls: () => calls };
}

for (const [name, steps, options] of [
  ['interrupted', [{ error: true }], {}],
  ['checksum', [{ body: Buffer.from('partial') }], {}],
  ['oversize', [{}], { maxBytes: 2 }],
  ['deadline', [{ stall: true }], { timeoutMs: 30 }],
  ['redirect loop', [{ status: 302, headers: { location: '/loop' } }], { maxRedirects: 2 }],
  ['HTTP error', [{ status: 500 }], {}],
]) {
  test(`${name} preserves previous cache, cleans partials, permits retry`, async (t) => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-download-test-'));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const destination = path.join(directory, 'python.tar.gz');
    const old = Buffer.from('previous good cache for an older checksum');
    await fs.writeFile(destination, old);
    const failed = fixture(steps);
    await assert.rejects(downloadPython('https://fixtures.invalid/python', destination, sha,
      { get: failed.get, ...options }));
    assert.deepEqual(await fs.readFile(destination), old);
    assert.deepEqual(await fs.readdir(directory), ['python.tar.gz']);
    if (name === 'redirect loop') assert.equal(failed.calls(), 3);
    await downloadPython('https://fixtures.invalid/python', destination, sha, fixture([{}]));
    assert.deepEqual(await fs.readFile(destination), content);
    assert.deepEqual(await fs.readdir(directory), ['python.tar.gz']);
    await downloadPython('https://fixtures.invalid/python', destination, sha, {
      get() { throw new Error('valid cache must not be downloaded again'); },
    });
  });
}

test('relative HTTPS redirects and new cache publication', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-download-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const destination = path.join(directory, 'python.tar.gz');
  const source = fixture([{ status: 302, headers: { location: '/archive' } }, {}]);
  await downloadPython('https://fixtures.invalid/start', destination, sha, source);
  assert.equal(source.calls(), 2);
  assert.deepEqual(await fs.readFile(destination), content);
});

test('staging cleanup failure leaves the published cache usable and reports the staging path', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-download-test-'));
  const rm = fs.rm;
  t.after(() => rm(directory, { recursive: true, force: true }));
  const destination = path.join(directory, 'python.tar.gz');
  await fs.writeFile(destination, 'previous cache');
  const warnings = t.mock.method(console, 'warn', () => {});
  let staging;
  t.mock.method(fs, 'rm', async (file, options) => {
    if (path.dirname(file) === directory && path.basename(file).startsWith('.python-download-')) {
      staging = file;
      throw new Error('injected removal failure');
    }
    return rm(file, options);
  });

  await downloadPython('https://fixtures.invalid/python', destination, sha, fixture([{}]));
  assert.equal(await checksum(destination, sha, {
    maxBytes: content.length, signal: new AbortController().signal,
  }), true);
  assert.ok((await fs.stat(staging)).isDirectory());
  assert.equal(warnings.mock.callCount(), 1);
  const [message] = warnings.mock.calls[0].arguments;
  assert.match(message, /staging cleanup failed/);
  assert.ok(message.includes(destination));
  assert.ok(message.includes(staging));
  assert.match(message, /injected removal failure/);
});

for (const phase of ['candidate', 'directory']) {
  test(`${phase} sync failure reports the cache publication outcome`, {
    skip: phase === 'directory' && process.platform === 'win32',
  }, async (t) => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-download-test-'));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const destination = path.join(directory, 'python.tar.gz');
    const old = Buffer.from('previous cache');
    await fs.writeFile(destination, old);

    const syncError = Object.assign(new Error('injected sync failure'), { code: 'EIO' });
    const open = fs.open;
    let failSync = true;
    const visible = phase === 'candidate' ? old : content;
    t.mock.method(fs, 'open', async (file, ...args) => {
      const handle = await open(file, ...args);
      const matches = phase === 'candidate' ? path.basename(file) === 'archive' : file === directory;
      if (matches && failSync) {
        handle.sync = async () => {
          assert.deepEqual(await fs.readFile(destination), visible);
          throw syncError;
        };
      }
      return handle;
    });

    const download = (source) => downloadPython('https://fixtures.invalid/python', destination, sha, source);
    if (phase === 'candidate') {
      await assert.rejects(download(fixture([{}])), (error) => error === syncError);
    } else {
      const uncertain = (error) => error.cause === syncError &&
        error.message.includes(destination) && /durability is uncertain/.test(error.message);
      await assert.rejects(download(fixture([{}])), uncertain);
      await assert.rejects(download({
        get() { throw new Error('valid cache must not be downloaded again'); },
      }), uncertain);
    }
    assert.deepEqual(await fs.readFile(destination), visible);
    assert.deepEqual(await fs.readdir(directory), ['python.tar.gz']);

    failSync = false;
    const retry = fixture([{}]);
    await download(retry);
    assert.equal(retry.calls(), phase === 'candidate' ? 1 : 0);
    assert.deepEqual(await fs.readFile(destination), content);
  });
}

for (const code of ['EINVAL', 'ENOTSUP', 'EOPNOTSUPP', 'ENOSYS', 'ENOTTY']) {
  test(`cache publication supports filesystems without directory sync (${code})`, {
    skip: process.platform === 'win32',
  }, async (t) => {
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-download-test-'));
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const destination = path.join(directory, 'python.tar.gz');
    const open = fs.open;
    let directorySyncs = 0;
    t.mock.method(fs, 'open', async (file, ...args) => {
      const handle = await open(file, ...args);
      if (file === directory) {
        handle.sync = async () => {
          directorySyncs++;
          throw Object.assign(new Error('directory sync unsupported'), { code });
        };
      }
      return handle;
    });
    await downloadPython('https://fixtures.invalid/python', destination, sha, fixture([{}]));
    assert.equal(directorySyncs, 1);
    assert.deepEqual(await fs.readFile(destination), content);
  });
}
