const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const Module = require('node:module');
const path = require('node:path');
const { test } = require('node:test');

function deferred() {
  let resolve; let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }

function loadMain({ port = Promise.resolve(8123) } = {}) {
  const ready = deferred(); const healthy = deferred(); const stopped = deferred();
  const windows = []; const spawned = []; const stops = [];
  const backend = new EventEmitter(); backend.pid = 1234; backend.exitCode = null;
  backend.stdout = new EventEmitter(); backend.stderr = new EventEmitter();
  backend.kill = () => { throw new Error('Main must use tree shutdown owner'); };
  let completedQuits = 0;
  let signal;
  const app = new EventEmitter(); app.isPackaged = false;
  app.requestSingleInstanceLock = () => true;
  app.whenReady = () => ready.promise;
  app.quit = () => {
    let prevented = false;
    app.emit('before-quit', { preventDefault() { prevented = true; } });
    if (!prevented) { completedQuits++; app.emit('will-quit'); }
  };
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super(); this.options = options; windows.push(this);
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
    }
    loadURL(url) { this.url = url; }
  }
  const electron = {
    app, BrowserWindow, Notification: class {}, shell: {},
    ipcMain: { handle() {}, removeHandler() {} },
  };
  const originalLoad = Module._load;
  const main = path.join(__dirname, '..', 'main.js'); delete require.cache[main];
  Module._load = function mock(request, ...rest) {
    if (request === 'electron') return electron;
    if (request === 'fs') return { existsSync: () => false };
    if (request === 'child_process') return {
      execSync() {}, spawn(...args) { spawned.push(args); return backend; },
    };
    if (request === './backend-port') return () => port;
    if (request === './backend-readiness') return {
      waitForBackend(url, options) {
        assert.equal(url, 'http://127.0.0.1:8123/'); signal = options.signal;
        signal.addEventListener('abort', () => healthy.reject(new Error('cancelled')), { once: true });
        return healthy.promise;
      },
    };
    if (request === './backend-shutdown') return {
      stopBackend(child) { stops.push(child); return stopped.promise; },
    };
    return originalLoad.call(this, request, ...rest);
  };
  try { require(main); } finally { Module._load = originalLoad; }
  return { app, ready, healthy, stopped, windows, spawned, stops, backend,
    get signal() { return signal; }, get completedQuits() { return completedQuits; } };
}

test('quit during startup cancels readiness, blocks late windows and waits for one tree shutdown', async () => {
  const shell = loadMain(); shell.ready.resolve(); await flush();
  assert.equal(shell.spawned.length, 1);
  assert.deepEqual(shell.spawned[0][2].stdio, ['ignore', 'pipe', 'pipe']);
  shell.app.emit('activate'); assert.equal(shell.windows.length, 0);
  process.emit('SIGINT'); process.emit('SIGINT');
  assert.equal(shell.signal.aborted, true);
  assert.equal(shell.stops.length, 1); assert.equal(shell.stops[0], shell.backend);
  assert.equal(shell.completedQuits, 0);
  shell.healthy.resolve(); await flush();
  assert.equal(shell.windows.length, 0);
  shell.stopped.resolve(); await flush(); assert.equal(shell.completedQuits, 1);
});

test('healthy backend opens once; even a zero-status backend exit cancels and shuts down', async () => {
  const shell = loadMain(); shell.ready.resolve(); await flush();
  shell.healthy.resolve(); await flush();
  assert.equal(shell.windows.length, 1);
  assert.equal(shell.windows[0].options.webPreferences.contextIsolation, true);
  shell.backend.emit('exit', 0);
  assert.equal(shell.signal.aborted, true); assert.equal(shell.stops.length, 1);
  shell.app.emit('activate'); assert.equal(shell.windows.length, 1);
  shell.stopped.resolve(); await flush(); assert.equal(shell.completedQuits, 1);
});

test('startup failure follows the same bounded tree shutdown path', async () => {
  const shell = loadMain(); shell.ready.resolve(); await flush();
  const log = console.error; console.error = () => {};
  try { shell.healthy.reject(new Error('synthetic failure')); await flush(); }
  finally { console.error = log; }
  assert.equal(shell.stops.length, 1); assert.equal(shell.windows.length, 0);
  shell.stopped.resolve(); await flush(); assert.equal(shell.completedQuits, 1);
});

test('quit while port selection is pending never spawns a backend', async () => {
  const port = deferred(); const shell = loadMain({ port: port.promise });
  shell.ready.resolve(); await flush(); shell.app.quit();
  port.resolve(8123); await flush(); assert.equal(shell.spawned.length, 0);
  shell.stopped.resolve(); await flush(); assert.equal(shell.completedQuits, 1);
});
