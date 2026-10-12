const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
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
      this.loaded = [];
      windows.push(this);
    }

    loadURL(url) {
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
  try {
    require(mainPath);
  } finally {
    Module._load = originalLoad;
  }
  return { app, handlers, opened, notifications, windows };
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
