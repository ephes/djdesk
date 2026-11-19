const assert = require('node:assert/strict');
const path = require('node:path');
const { test } = require('node:test');
const { buildFixture, setEnv } = require('./build-fixture.cjs');

for (const stream of ['stdout', 'stderr']) {
  test(`interpreter version accepts expected output on ${stream}`, (t) => {
    const calls = [];
    const { root, build } = buildFixture(t, {
      spawnSync: (...args) => {
        calls.push(args);
        return { status: 0, stdout: '', stderr: '', [stream]: 'Python 3.14.0\n' };
      }
    });
    const python = path.join(root, 'standalone python');
    build.ensurePythonVersion(python);
    assert.deepEqual(calls, [[python, ['--version'], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 10000, killSignal: 'SIGKILL'
    }]]);
  });
}

for (const [result, message] of [
  [{ status: 0, stdout: 'Python 3.13.9', stderr: '' }, /Unexpected Python version/],
  [{ status: 1, stdout: '', stderr: 'synthetic version failure' }, /synthetic version failure/],
  [{ status: null, error: new Error('synthetic missing executable') }, /synthetic missing executable/],
]) {
  test(`rejects missing/wrong interpreter output (${result.status})`, (t) => {
    const { build } = buildFixture(t, { spawnSync: () => result });
    assert.throws(() => build.ensurePythonVersion('fixture-python'), message);
  });
}

for (const [label, result, message] of [
  ['nonzero exit with the expected version', { status: 7 }, /status 7/],
  ['signal with the expected version', { status: null, signal: 'SIGTERM' }, /SIGTERM/],
  ['longer version', { status: 0, stdout: 'Python 3.14.01\n' }, /Unexpected Python version/],
  ['version suffix', { status: 0, stdout: 'Python 3.14.0rc1\n' }, /Unexpected Python version/],
  ['timeout', { status: null, error: new Error('synthetic ETIMEDOUT') }, /ETIMEDOUT/],
]) {
  test(`interpreter probe rejects ${label}`, (t) => {
    const { build } = buildFixture(t, { spawnSync: () => ({
      stdout: 'Python 3.14.0\n', stderr: '', ...result
    }) });
    assert.throws(() => build.ensurePythonVersion('fixture-python'), message);
  });
}

test('Django verification uses the bundled interpreter and sources', async (t) => {
  const calls = [];
  const { root, bundle, build } = buildFixture(t, {
    spawnSync: (...args) => {
      calls.push(args);
      return { status: 0, stdout: '5.2.19\n', stderr: '' };
    }
  });
  const existingPath = path.join(root, 'existing sources');
  setEnv(t, { PYTHONPATH: existingPath });
  const python = path.join(bundle, 'python executable');
  await build.verifyBundle(python);
  assert.equal(calls.length, 1);
  const [command, args, options] = calls[0];
  assert.equal(command, python);
  assert.deepEqual(args, ['-c', 'import django, sys; sys.stdout.write(django.get_version())']);
  assert.equal(options.cwd, bundle);
  assert.equal(options.env.PYTHONPATH, `${path.join(bundle, 'src')}${path.delimiter}${existingPath}`);
  assert.equal(process.env.PYTHONPATH, existingPath);
  assert.equal(options.encoding, 'utf8');
  assert.equal(options.stdio, 'pipe');
});

for (const result of [
  { status: 1, stdout: '', stderr: 'synthetic import failure\n' },
  { status: 2, stdout: 'synthetic stdout failure\n', stderr: '' },
  { status: null, stdout: null, stderr: null, error: new Error('synthetic spawn error') }
]) {
  test(`Django verification rejects failed import (${result.status})`, async (t) => {
    const { build } = buildFixture(t, { spawnSync: () => result });
    await assert.rejects(build.verifyBundle('fixture-python'), (error) => {
      assert.match(error.message, new RegExp(`Django verification failed \\(status ${result.status}\\)`));
      if (result.stderr || result.stdout) {
        assert.ok(error.message.includes((result.stderr || result.stdout).trim()));
      }
      return true;
    });
  });
}
