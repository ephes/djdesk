const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { test } = require('node:test');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { buildFixture } = require('./build-fixture.cjs');

const names = ['prepareStandalonePython', 'installPythonDeps', 'copyProjectFiles',
  'collectStatic', 'writeLauncherScript', 'writeVersionFile', 'verifyBundle'];

function stepsFor(destination, fail) {
  let staging;
  return Object.fromEntries(names.map((name) => [name, async (...args) => {
    if (name === 'prepareStandalonePython') {
      staging = args[1];
      assert.notEqual(staging, destination, 'build must use its own staging root');
    }
    if (name === fail) throw new Error(`synthetic ${name}`);
    if (name !== 'installPythonDeps') assert.equal(args.at(-1), staging);
    await fs.writeFile(path.join(staging, name), 'complete');
    return path.join(staging, 'python');
  }]));
}

for (const fail of names) {
  test(`${fail} failure retains the complete previous runtime`, async (t) => {
    const { bundle, build } = buildFixture(t);
    await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    await assert.rejects(build.main({ steps: stepsFor(bundle, fail) }), new RegExp(`synthetic ${fail}`));
    assert.deepEqual(await fs.readdir(bundle), ['previous']);
    assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
    assert.deepEqual(await fs.readdir(path.dirname(bundle)), ['django-bundle']);
  });
}

test('publishes a complete verified generation and retires previous runtime', async (t) => {
  const { bundle, build } = buildFixture(t);
  await fs.writeFile(path.join(bundle, 'previous'), 'last good');
  await build.main({ steps: stepsFor(bundle) });
  assert.deepEqual((await fs.readdir(bundle)).sort(), [...names].sort());
  assert.deepEqual(await fs.readdir(path.dirname(bundle)), ['django-bundle']);
});

for (const code of ['EIO', 'ENOENT']) {
  test(`backup rename that reports ${code} after moving restores the previous runtime`, async (t) => {
    const { bundle, build } = buildFixture(t);
    await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    const failure = Object.assign(new Error('synthetic backup failure'), { code });
    const io = { ...fs, async rename(source, target) {
      await fs.rename(source, target);
      if (source === bundle) throw failure;
    } };
    await assert.rejects(build.main({ steps: stepsFor(bundle), io }), (error) => error === failure);
    assert.deepEqual(await fs.readdir(bundle), ['previous']);
    assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
    assert.deepEqual(await fs.readdir(path.dirname(bundle)), ['django-bundle']);
  });
}

for (const hasPrevious of [true, false]) {
  test(`installed candidate wins after a promotion error (previous: ${hasPrevious})`, async (t) => {
    const { bundle, build } = buildFixture(t);
    if (hasPrevious) {
      await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    } else {
      await fs.rmdir(bundle);
    }
    const unrelated = path.join(path.dirname(bundle), 'unrelated');
    await fs.writeFile(unrelated, 'keep');
    const io = { ...fs, async rename(source, target) {
      await fs.rename(source, target);
      if (path.basename(source) === 'staging') throw new Error('synthetic completed promotion');
    } };
    await build.main({ steps: stepsFor(bundle), io });
    assert.deepEqual((await fs.readdir(bundle)).sort(), [...names].sort());
    assert.deepEqual((await fs.readdir(path.dirname(bundle))).sort(), ['django-bundle', 'unrelated']);
    assert.equal(await fs.readFile(unrelated, 'utf8'), 'keep');
  });
}

test('uncertain promotion preserves an unrelated destination and the old backup', async (t) => {
  const { bundle, build } = buildFixture(t);
  await fs.writeFile(path.join(bundle, 'previous'), 'last good');
  const replacement = new Error('synthetic unrelated promotion');
  const io = { ...fs, async rename(source, target) {
    if (path.basename(source) === 'staging') {
      await fs.mkdir(target);
      throw replacement;
    }
    return fs.rename(source, target);
  } };
  await assert.rejects(build.main({ steps: stepsFor(bundle), io }), (error) => error === replacement);
  assert.deepEqual(await fs.readdir(bundle), []);
  assert.equal(await fs.readFile(path.join(replacement.recoveryPath, 'previous'), 'utf8'), 'last good');
});

for (const restoreFails of [false, true]) {
  test(`promotion failure ${restoreFails ? 'retains recovery copy' : 'restores previous runtime'}`, async (t) => {
    const { bundle, build } = buildFixture(t);
    await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    const io = { ...fs, async rename(source, target) {
      if (target === bundle && (restoreFails || path.basename(source) === 'staging')) {
        throw new Error('synthetic promotion');
      }
      return fs.rename(source, target);
    } };
    let failure;
    await assert.rejects(build.main({ steps: stepsFor(bundle), io }), (error) => {
      failure = error;
      return /synthetic promotion/.test(error.message);
    });
    if (restoreFails) {
      assert.ok(failure.recoveryPath);
      assert.equal(await fs.readFile(path.join(failure.recoveryPath, 'previous'), 'utf8'), 'last good');
    } else {
      assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
      assert.deepEqual(await fs.readdir(path.dirname(bundle)), ['django-bundle']);
    }
  });
}

const workerSource = `
const fs = require('node:fs/promises');
const path = require('node:path');
const { main } = require(process.argv[1]);
let staging;
const steps = {};
for (const name of ${JSON.stringify(names)}) steps[name] = async (...args) => {
  if (name === 'prepareStandalonePython') staging = args[1];
  await fs.writeFile(path.join(staging, name), process.env.WORKER);
  if (name === 'verifyBundle') {
    process.send('ready');
    await new Promise(resolve => process.once('message', resolve));
    if (process.env.FAIL) throw new Error('synthetic owner failure');
  }
  return path.join(staging, 'python');
};
main({ destination: process.argv[2], steps }).then(
  () => { process.send('complete'); process.disconnect(); },
  error => { process.send(error.message); process.disconnect(); process.exitCode = 1; }
);
`;

for (const ownerFails of [false, true]) {
  test(`concurrent builder refuses admission; owner ${ownerFails ? 'failure releases lock' : 'publishes alone'}`, async (t) => {
    const { root, bundle } = buildFixture(t);
    await fs.writeFile(path.join(bundle, 'previous'), 'last good');
    const alias = path.join(root, 'parent-alias');
    await fs.symlink(path.dirname(bundle), alias, process.platform === 'win32' ? 'junction' : 'dir');
    function start(worker, fail = false) {
      const child = spawn(process.execPath, ['-e', workerSource,
        path.resolve(__dirname, '../build-django.js'), worker === 'competitor'
          ? path.join(alias, 'django-bundle') : bundle], {
        env: { ...process.env, WORKER: worker, FAIL: fail ? '1' : '' },
        stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
      });
      t.after(() => { if (child.exitCode === null) child.kill(); });
      return { child, message: once(child, 'message'), exit: once(child, 'exit') };
    }
    const owner = start('owner', ownerFails);
    assert.equal((await owner.message)[0], 'ready');
    const competitor = start('competitor');
    assert.match((await competitor.message)[0], /already building/);
    assert.equal((await competitor.exit)[0], 1);
    assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
    const done = once(owner.child, 'message');
    owner.child.send('continue');
    assert.match((await done)[0], ownerFails ? /synthetic owner failure/ : /complete/);
    assert.equal((await owner.exit)[0], ownerFails ? 1 : 0);
    if (ownerFails) {
      assert.equal(await fs.readFile(path.join(bundle, 'previous'), 'utf8'), 'last good');
      const retry = start('retry');
      assert.equal((await retry.message)[0], 'ready');
      retry.child.send('continue');
      assert.equal((await retry.exit)[0], 0);
    }
    for (const name of names) {
      assert.equal(await fs.readFile(path.join(bundle, name), 'utf8'), ownerFails ? 'retry' : 'owner');
    }
    assert.deepEqual(await fs.readdir(path.dirname(bundle)), ['django-bundle']);
  });
}
