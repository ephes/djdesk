const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync, mkdtempSync, rmSync } = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const vm = require('node:vm');
const { test } = require('node:test');

const mainPath = path.join(__dirname, '..', 'main.js');
const realRequire = createRequire(mainPath);

for (const packaged of [false, true]) {
  test(`Django spawn ${packaged ? 'uses per-user packaged state' : 'preserves development defaults'}`, async (t) => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'djdesk state '));
    t.after(() => rmSync(root, { recursive: true, force: true }));
    const state = path.join(root, 'user data');
    const calls = [], paths = [];
    const app = new EventEmitter();
    Object.assign(app, {
      isPackaged: packaged, requestSingleInstanceLock: () => true,
      whenReady: () => new Promise(() => {}), quit() {},
      getPath(name) { paths.push(name); return state; },
    });
    const child = new EventEmitter();
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    const context = {
      __dirname: path.dirname(mainPath), console: { log() {}, error() {} },
      process: { platform: 'synthetic', resourcesPath: path.join(root, 'resources'), env: {} },
      setTimeout() {}, // Stop at the readiness boundary; no socket or Electron process.
      require(name) {
        if (name === 'electron') return { app, ipcMain: { handle() {}, removeHandler() {} } };
        if (name === 'fs') return { existsSync: () => true };
        if (name === 'child_process') return {
          spawn(binary, args, options) { calls.push({ binary, args, options }); return child; },
        };
        return realRequire(name);
      },
    };
    const api = vm.runInNewContext(readFileSync(mainPath, 'utf8') +
      '\n({ startDjango, setPort: (fn) => { getPortModule = fn; } });', context);
    api.setPort(async () => 8765);
    api.startDjango();
    await new Promise(setImmediate);
    assert.equal(calls.length, 1);
    assert.deepEqual(paths, packaged ? ['userData'] : []);
    assert.equal(calls[0].options.env.DJDESK_STATE_ROOT, packaged ? state : undefined);
    assert.equal(calls[0].options.env.DJANGO_SETTINGS_MODULE, 'djdesk.settings.local');
  });
}
