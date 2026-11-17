// Synthetic Electron app for frame-boundaries.cjs; never calls native OS handlers.
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const {
  installNavigationGuards, installSessionPermissionGuards, isTrustedSender,
} = require('../boundary-guards');

app.commandLine.appendSwitch('use-fake-device-for-media-stream');
const origin = process.env.DJDESK_TEST_APP_ORIGIN;
global.frameFixture = { checks: [], requests: [], notifications: [] };

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '../preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  const session = window.webContents.session;
  installSessionPermissionGuards({
    setPermissionRequestHandler(handler) {
      session.setPermissionRequestHandler((contents, permission, callback, details) => {
        global.frameFixture.requests.push({ permission, url: details.requestingUrl });
        handler(contents, permission, callback, details);
      });
    },
    setPermissionCheckHandler(handler) {
      session.setPermissionCheckHandler((contents, permission, requestingOrigin, details) => {
        global.frameFixture.checks.push({ permission, origin: requestingOrigin });
        return handler(contents, permission, requestingOrigin, details);
      });
    },
  });
  installNavigationGuards(window.webContents, {
    getTrustedOrigin: () => origin,
    shell: { openExternal() { throw new Error('Unexpected external dispatch'); } },
  });
  ipcMain.handle('djdesk:notify', (event, options) => {
    if (!isTrustedSender(event, origin)) return false;
    global.frameFixture.notifications.push(options);
    return true;
  });
  await window.loadURL(origin);
});

app.on('window-all-closed', () => app.quit());
