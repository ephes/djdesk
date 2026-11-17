// Synthetic Electron app for frame-boundaries.cjs; never calls native OS handlers.
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const {
  createOpenExternalHandler, installNavigationGuards, installSessionPermissionGuards, isTrustedSender,
} = require('../boundary-guards');

app.commandLine.appendSwitch('use-fake-device-for-media-stream');
const origin = process.env.DJDESK_TEST_APP_ORIGIN;
global.frameFixture = {
  checks: [], requests: [], notifications: [], opened: [], popups: [], navigations: [],
};
let window;

app.whenReady().then(async () => {
  window = new BrowserWindow({
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
  const shell = { async openExternal(url) { global.frameFixture.opened.push(url); } };
  installNavigationGuards({
    on(name, handler) {
      window.webContents.on(name, (event, ...rest) => {
        if (name === 'will-navigate') {
          global.frameFixture.navigations.push({ url: event.url, initiator: event.initiator?.url ?? null });
        }
        return handler(event, ...rest);
      });
    },
    setWindowOpenHandler(handler) {
      window.webContents.setWindowOpenHandler((details) => {
        global.frameFixture.popups.push({ url: details.url, referrer: details.referrer });
        return handler(details);
      });
    },
  }, {
    getTrustedOrigin: () => origin,
    shell,
  });
  ipcMain.handle('djdesk:open-external', createOpenExternalHandler({
    shell, getTrustedOrigin: () => origin,
  }));
  ipcMain.handle('djdesk:notify', (event, options) => {
    if (!isTrustedSender(event, origin)) return false;
    global.frameFixture.notifications.push(options);
    return true;
  });
  await window.loadURL(origin);
});

app.on('window-all-closed', () => app.quit());
