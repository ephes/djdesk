const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const { test } = require('node:test');

/**
 * Load main.js against a fake `electron` module so the real handler wiring
 * (not just the guard helpers) is exercised without launching Electron.
 */
function loadMain() {
  const handlers = new Map();
  const opened = [];
  const notifications = [];
  const windows = [];
  const app = new EventEmitter();
  app.isPackaged = false;
  app.requestSingleInstanceLock = () => true;
  app.whenReady = () => new Promise(() => {});
  app.quit = () => {};

  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = (handler) => {
        this.webContents.windowOpenHandler = handler;
      };
      this.webContents.openDevTools = () => {};
      this.webContents.session = {
        setPermissionRequestHandler: (handler) => { this.requestPermission = handler; },
        setPermissionCheckHandler: (handler) => { this.checkPermission = handler; },
      };
      this.loaded = [];
      windows.push(this);
    }

    loadURL(url) {
      assert.equal(typeof this.requestPermission, 'function', 'request policy must precede loadURL');
      assert.equal(typeof this.checkPermission, 'function', 'check policy must precede loadURL');
      this.loaded.push(url);
    }
  }

  class Notification {
    constructor(options) {
      this.options = options;
    }

    show() {
      notifications.push(this.options);
    }
  }

  const electron = {
    app,
    BrowserWindow,
    Notification,
    ipcMain: {
      handle: (channel, handler) => handlers.set(channel, handler),
      removeHandler: (channel) => handlers.delete(channel),
    },
    shell: {
      openExternal: async (url) => {
        opened.push(url);
      },
    },
  };

  const mainPath = path.join(__dirname, '..', 'main.js');
  delete require.cache[mainPath];
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request, ...rest) {
    if (request === 'electron') {
      return electron;
    }
    return originalLoad.call(this, request, ...rest);
  };
  let entry;
  try {
    entry = new Module(mainPath, module);
    entry.filename = mainPath;
    entry.paths = Module._nodeModulePaths(path.dirname(mainPath));
    // Expose lexical startup functions only in this test copy of the entrypoint.
    entry._compile(fs.readFileSync(mainPath, 'utf8') + `
      module.exports = { createWindow, setPort(port) { djangoPort = port; } };
    `, mainPath);
  } finally {
    Module._load = originalLoad;
  }
  return { app, handlers, opened, notifications, windows, ...entry.exports };
}

function frameEvent(url) {
  const frame = { url, parent: null };
  return { senderFrame: frame, sender: { mainFrame: frame } };
}

test('main.js IPC handlers refuse requests before Django is running', async () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    const { handlers, opened, notifications } = loadMain();
    const openExternal = handlers.get('djdesk:open-external');
    const notify = handlers.get('djdesk:notify');
    assert.equal(typeof openExternal, 'function');
    assert.equal(typeof notify, 'function');

    const event = frameEvent('http://127.0.0.1:8000/');
    assert.equal(await openExternal(event, 'https://example.com/'), false);
    assert.equal(await openExternal(event, 'file:///etc/passwd'), false);
    assert.equal(await notify(event, { title: 'x', body: 'y' }), false);
    assert.deepEqual(opened, []);
    assert.deepEqual(notifications, []);
  } finally {
    console.warn = warn;
  }
});

test('window session policies precede loading and preserve validated native notifications', async () => {
  const { setPort, createWindow, windows, handlers, notifications } = loadMain();
  setPort(8000);
  createWindow();
  const window = windows[0];
  assert.deepEqual(window.loaded, ['http://127.0.0.1:8000/']);
  assert.equal(window.checkPermission(null, 'notifications', 'https://remote-docs.invalid'), false);
  const replies = [];
  window.requestPermission(null, 'media', (allowed) => replies.push(allowed));
  assert.deepEqual(replies, [false]);
  const notify = handlers.get('djdesk:notify');
  assert.equal(await notify(frameEvent(window.loaded[0]), { title: 'Synthetic task', body: 'Done' }), true);
  assert.deepEqual(notifications, [{ title: 'Synthetic task', body: 'Done' }]);
});
