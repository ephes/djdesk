const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const { test } = require('node:test');
const { downloadPython } = require('../python-download');

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
