const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { buildFixture, pythonExecutable } = require('./build-fixture.cjs');

test('importing the builder does not recreate a bundle or start a build', async (t) => {
  const { bundle, build } = buildFixture(t);
  const sentinel = path.join(bundle, 'sentinel');
  fs.writeFileSync(sentinel, 'preserve');
  await new Promise(setImmediate);
  assert.equal(fs.readFileSync(sentinel, 'utf8'), 'preserve');
  assert.equal(typeof build.writeLauncherScript, 'function');
  assert.equal(fs.existsSync(path.join(bundle, 'run_django.py')), false);
});

for (const failMigrate of [false, true]) {
  test(`generated launcher ${failMigrate ? 'stops after failed migration' : 'migrates before serving'}`, async (t) => {
    const { root, bundle, build } = buildFixture(t);
    const src = path.join(bundle, 'src');
    const management = path.join(src, 'django', 'core', 'management');
    fs.mkdirSync(management, { recursive: true });
    for (const dir of [path.join(src, 'django'), path.join(src, 'django', 'core')]) {
      fs.writeFileSync(path.join(dir, '__init__.py'), '');
    }
    fs.writeFileSync(path.join(management, '__init__.py'), `
import json
import os
import sys

def execute_from_command_line(argv):
    with open(os.environ['COMMAND_LOG'], 'a', encoding='utf-8') as log:
        log.write(json.dumps(dict(argv=argv, sys_argv=sys.argv, cwd=os.getcwd(),
            pythonpath=os.environ['PYTHONPATH'], sys_path=sys.path,
            settings=os.environ['DJANGO_SETTINGS_MODULE'],
            env=os.environ['DJANGO_ENV'])) + '\\n')
    if argv[1] == 'migrate' and os.environ['FAIL_MIGRATE'] == '1':
        raise RuntimeError('synthetic migration failure')
`);
    await build.writeLauncherScript();
    const log = path.join(root, 'commands.jsonl');
    const existingPath = path.join(root, 'existing python path');
    const result = spawnSync(pythonExecutable(), [
      path.join(bundle, 'run_django.py'), '--host', '192.0.2.10', '--port', '9432'
    ], {
      cwd: root,
      encoding: 'utf8',
      env: {
        // Deliberately exclude ambient Python/Django configuration.
        PATH: process.env.PATH,
        SYSTEMROOT: process.env.SYSTEMROOT,
        PYTHONPATH: existingPath,
        COMMAND_LOG: log,
        FAIL_MIGRATE: failMigrate ? '1' : '0'
      }
    });
    assert.equal(result.error, undefined);
    if (failMigrate) {
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /synthetic migration failure/);
    } else {
      assert.equal(result.status, 0, result.stderr);
    }
    const calls = fs.readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.map((call) => call.argv), failMigrate ? [
      ['manage.py', 'migrate', '--noinput']
    ] : [
      ['manage.py', 'migrate', '--noinput'],
      ['manage.py', 'runserver', '192.0.2.10:9432', '--noreload']
    ]);
    for (const call of calls) {
      assert.deepEqual(call.sys_argv, call.argv);
      assert.equal(call.cwd, fs.realpathSync(bundle));
      assert.equal(call.pythonpath, `${fs.realpathSync(src)}${path.delimiter}${existingPath}`);
      assert.equal(call.sys_path[0], fs.realpathSync(src));
      assert.equal(call.settings, 'djdesk.settings.local');
      assert.equal(call.env, 'local');
    }
  });
}
