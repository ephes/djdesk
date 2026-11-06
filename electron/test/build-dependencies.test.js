const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { buildFixture, setEnv } = require('./build-fixture.cjs');

function processFixture(t, outcome = 0) {
  const calls = [];
  const fixture = buildFixture(t, {
    spawn: (command, args, options) => {
      calls.push({ command, args, options });
      const child = new EventEmitter();
      queueMicrotask(() => {
        if (outcome instanceof Error) child.emit('error', outcome);
        else child.emit('close', outcome);
      });
      return child;
    }
  });
  return { ...fixture, calls };
}

const pyproject = `
[project]
name = "invented-bundle"
dependencies = ["example-runtime>=2", "other-runtime[feature]==3.1"]
[project.optional-dependencies]
dev = ["example-dev==9"]
[dependency-groups]
dev = ["group-dev==8"]
`;

test('installs only project runtime requirements with the bundled Python', async (t) => {
  setEnv(t, { PIP_REQUIRE_VIRTUALENV: '1', DJDESK_FIXTURE_MARKER: 'inherited' });
  const { root, build, calls } = processFixture(t);
  fs.writeFileSync(path.join(root, 'pyproject.toml'), pyproject);
  assert.deepEqual(build.readProjectDependencies(), [
    'example-runtime>=2', 'other-runtime[feature]==3.1'
  ]);
  const python = path.join(root, 'bundled python');
  await build.installPythonDeps(python);
  assert.equal(calls.length, 1);
  const { command, args, options } = calls[0];
  assert.equal(command, python);
  assert.deepEqual(args, [
    '-m', 'pip', 'install', '--no-cache-dir',
    'example-runtime>=2', 'other-runtime[feature]==3.1'
  ]);
  assert.equal(options.cwd, root);
  assert.equal(options.env.PIP_REQUIRE_VIRTUALENV, '0');
  assert.equal(options.env.DJDESK_FIXTURE_MARKER, 'inherited');
  assert.equal(process.env.PIP_REQUIRE_VIRTUALENV, '1');
  assert.equal(options.shell, false);
  assert.equal(options.stdio, 'inherit');
});

for (const declaration of ['', '[project]\nname = "empty"', '[project]\ndependencies = []']) {
  test(`no pip process for absent/empty runtime requirements: ${JSON.stringify(declaration)}`, async (t) => {
    const { root, build, calls } = processFixture(t);
    fs.writeFileSync(path.join(root, 'pyproject.toml'), declaration);
    assert.deepEqual(build.readProjectDependencies(), []);
    await build.installPythonDeps('unused-python');
    assert.deepEqual(calls, []);
  });
}

for (const configured of [false, true]) {
  test(`collectstatic targets the bundle (${configured ? 'configured' : 'default'} settings)`, async (t) => {
    const { root, bundle, build, calls } = processFixture(t);
    const previousPath = path.join(root, 'extra sources');
    setEnv(t, {
      DJANGO_STATIC_ROOT: path.join(root, 'do not write here'),
      DJANGO_ENV: configured ? 'fixture' : undefined,
      DJANGO_SETTINGS_MODULE: configured ? 'fixture.settings' : undefined,
      PYTHONPATH: previousPath
    });
    const python = path.join(bundle, 'python executable');
    await build.collectStatic(python);
    assert.equal(calls.length, 1);
    const { command, args, options } = calls[0];
    assert.equal(command, python);
    assert.deepEqual(args, [path.join(root, 'manage.py'), 'collectstatic', '--no-input', '--clear']);
    assert.equal(options.cwd, root);
    assert.equal(options.env.DJANGO_STATIC_ROOT, path.join(bundle, 'staticfiles'));
    assert.equal(options.env.PYTHONPATH, `${path.join(root, 'src')}${path.delimiter}${previousPath}`);
    assert.equal(options.env.DJANGO_ENV, configured ? 'fixture' : 'local');
    assert.equal(options.env.DJANGO_SETTINGS_MODULE, configured ? 'fixture.settings' : 'djdesk.settings.local');
    assert.equal(process.env.DJANGO_STATIC_ROOT, path.join(root, 'do not write here'));
    assert.equal(options.shell, false);
  });
}

for (const operation of ['installPythonDeps', 'collectStatic']) {
  for (const outcome of [7, new Error('synthetic spawn failure')]) {
    test(`${operation} propagates ${outcome instanceof Error ? 'spawn errors' : 'nonzero exit'}`, async (t) => {
      const { root, build, calls } = processFixture(t, outcome);
      fs.writeFileSync(path.join(root, 'pyproject.toml'), pyproject);
      await assert.rejects(build[operation]('fixture-python'),
        outcome instanceof Error ? /synthetic spawn failure/ : /exited with status 7/);
      assert.equal(calls.length, 1);
    });
  }
}
