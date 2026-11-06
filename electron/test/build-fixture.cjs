const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const { compileFunction } = require('node:vm');
const { spawnSync } = require('node:child_process');

const buildPath = path.join(__dirname, '..', 'build-django.js');
const realRequire = createRequire(buildPath);

function buildFixture(t, processes = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'djdesk build contracts '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bundle = path.join(root, 'electron', 'django-bundle');
  fs.mkdirSync(bundle, { recursive: true });

  // Compile the unchanged CommonJS module at a fixture location so every build
  // path stays in scratch space. Only subprocess boundaries are replaced.
  const loaded = { exports: {} };
  const fixtureRequire = (request) => {
    if (request === 'child_process') {
      const unexpected = () => { throw new Error('Unexpected build subprocess'); };
      return { spawn: unexpected, spawnSync: unexpected, execSync: unexpected, ...processes };
    }
    return realRequire(request);
  };
  fixtureRequire.main = require.main;
  const filename = path.join(root, 'electron', 'build-django.js');
  const executeModule = compileFunction(fs.readFileSync(buildPath, 'utf8'),
    ['require', 'module', 'exports', '__filename', '__dirname'], { filename });
  executeModule(fixtureRequire, loaded, loaded.exports, filename, path.dirname(filename));
  return { root, bundle, build: loaded.exports };
}

function setEnv(t, values) {
  for (const [key, value] of Object.entries(values)) {
    const previous = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
    t.after(() => {
      if (previous === undefined) delete process.env[key];
      else process.env[key] = previous;
    });
  }
}

function pythonExecutable() {
  // Use an existing interpreter; never download one. No platform-specific skip.
  for (const command of [process.env.PYTHON, 'python3', 'python'].filter(Boolean)) {
    const result = spawnSync(command, ['-c', 'import sys; print(sys.executable)'], {
      encoding: 'utf8'
    });
    if (result.status === 0) return result.stdout.trim();
  }
  assert.fail('Build contract tests require Python 3 on PATH or PYTHON');
}

module.exports = { buildFixture, setEnv, pythonExecutable };
