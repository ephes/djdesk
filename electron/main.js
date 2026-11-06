const { app, BrowserWindow, ipcMain, Notification, shell } = require('electron');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const getAvailablePort = require('./backend-port');
const { waitForBackend } = require('./backend-readiness');
const { stopBackend } = require('./backend-shutdown');
const {
  buildTrustedOrigin,
  createOpenExternalHandler,
  installNavigationGuards,
  isTrustedSender,
} = require('./boundary-guards');

const resourceBase = app.isPackaged ? process.resourcesPath : __dirname;
const DJANGO_BUNDLE_DIR = path.join(resourceBase, 'django-bundle');
const BUNDLE_RUNNER = path.join(DJANGO_BUNDLE_DIR, 'run_django.py');

let mainWindow = null;
let djangoProcess;
let djangoPort;
let quitting = false;
let backendReady = false;
let quitAllowed = false;
let shutdown;
const startup = new AbortController();
const quitOnSignal = () => app.quit();
const quitSignals = process.platform === 'win32' ? ['SIGINT', 'SIGTERM'] : ['SIGINT', 'SIGTERM', 'SIGHUP'];
for (const signal of quitSignals) process.on(signal, quitOnSignal);
app.on('will-quit', () => {
  for (const signal of quitSignals) process.removeListener(signal, quitOnSignal);
});

const IPC_CHANNELS = {
  OPEN_EXTERNAL: 'djdesk:open-external',
  NOTIFY: 'djdesk:notify',
};

function getTrustedOrigin() {
  return buildTrustedOrigin(djangoPort);
}

function registerIpcHandlers() {
  ipcMain.handle(
    IPC_CHANNELS.OPEN_EXTERNAL,
    createOpenExternalHandler({ shell, getTrustedOrigin })
  );

  ipcMain.handle(IPC_CHANNELS.NOTIFY, (event, rawOptions) => {
    if (!isTrustedSender(event, getTrustedOrigin())) {
      console.warn('Blocked notification request from untrusted frame');
      return false;
    }
    const options = rawOptions || {};
    const title = typeof options.title === 'string' && options.title ? options.title : 'DJDesk';
    const body = typeof options.body === 'string' ? options.body : '';
    if (typeof Notification.isSupported === 'function' && !Notification.isSupported()) {
      return false;
    }
    try {
      const notification = new Notification({ title, body });
      notification.show();
      return true;
    } catch (error) {
      console.warn('Failed to show notification', error);
      return false;
    }
  });
}

function getBundledPythonPath() {
  const bundlePython = process.platform === 'win32'
    ? path.join(DJANGO_BUNDLE_DIR, 'python', 'python.exe')
    : path.join(DJANGO_BUNDLE_DIR, 'python', 'bin', 'python3');

  if (fs.existsSync(bundlePython) && fs.existsSync(BUNDLE_RUNNER)) {
    return bundlePython;
  }
  return null;
}

function findSystemPython() {
  if (process.env.PYTHON) {
    return process.env.PYTHON;
  }

  const candidates = ['python3.14', 'python3', 'python'];
  for (const cmd of candidates) {
    try {
      execSync(`${cmd} --version`, { stdio: 'ignore' });
      return cmd;
    } catch (err) {
      // Command not found, try next
    }
  }

  throw new Error(
    'Python not found. Please install Python 3.14+ or set the PYTHON environment variable.'
  );
}

function resolvePython() {
  const bundled = getBundledPythonPath();
  if (bundled) {
    return { interpreter: bundled, mode: 'bundled' };
  }
  return { interpreter: findSystemPython(), mode: 'system' };
}

// Ensure single instance
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  console.log('Another instance is already running. Exiting...');
  app.quit();
} else {
  registerIpcHandlers();
  app.on('second-instance', () => {
    // Someone tried to run a second instance, we should focus our window
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(startDjango);
}

async function startDjango() {
  try {
    if (quitting) return;

    // Find Python interpreter and determine run mode
    const pythonInfo = resolvePython();
    const isBundled = pythonInfo.mode === 'bundled';
    console.log(
      `Using ${isBundled ? 'bundled' : 'system'} Python: ${pythonInfo.interpreter}`
    );

    // Get an available port
    djangoPort = await getAvailablePort({ port: 8000 });
    if (quitting) return;
    console.log(`Starting Django on port ${djangoPort}...`);

    const managePyPath = app.isPackaged
      ? path.join(DJANGO_BUNDLE_DIR, 'manage.py')
      : path.join(__dirname, '..', 'manage.py');

    const djangoArgs = isBundled
      ? [
          BUNDLE_RUNNER,
          '--host',
          '127.0.0.1',
          '--port',
          String(djangoPort)
        ]
      : [
          managePyPath,
          'runserver',
          `127.0.0.1:${djangoPort}`,
          '--noreload'
        ];

    const djangoCwd = isBundled ? DJANGO_BUNDLE_DIR : path.join(__dirname, '..');
    const djangoEnv = {
      ...process.env,
      DJANGO_ENV: process.env.DJANGO_ENV || 'local',
      DJANGO_SETTINGS_MODULE: process.env.DJANGO_SETTINGS_MODULE || 'djdesk.settings.local'
    };

    // Spawn Django dev server
    djangoProcess = spawn(pythonInfo.interpreter, djangoArgs, {
      cwd: djangoCwd,
      env: djangoEnv,
      detached: process.platform !== 'win32',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    djangoProcess.stdout.on('data', (data) => {
      console.log(`Django: ${data.toString().trim()}`);
    });

    djangoProcess.stderr.on('data', (data) => {
      console.error(`Django: ${data.toString().trim()}`);
    });

    djangoProcess.on('error', (error) => {
      console.error('Failed to start Django:', error);
      killDjangoAndQuit();
    });

    djangoProcess.on('exit', (code) => {
      startup.abort();
      backendReady = false;
      console.log(`Django process exited with code ${code}`);
      if (!quitting) {
        app.quit();
      }
    });

    // Wait for Django to be ready
    await waitForBackend(`${getTrustedOrigin()}/`, { signal: startup.signal });
    if (quitting || startup.signal.aborted) return;
    backendReady = true;

    // Create the Electron window
    createWindow();
  } catch (error) {
    if (!quitting) {
      console.error('Error starting Django:', error);
      killDjangoAndQuit();
    }
  }
}

/**
 * Kill Django process and quit the app
 * Used for startup failures before app is fully ready
 */
function killDjangoAndQuit() {
  if (!quitting) app.quit();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true
    },
    title: 'DJDesk'
  });

  // Keep the privileged window on the local Django origin (see boundary-guards.js).
  installNavigationGuards(mainWindow.webContents, { shell, getTrustedOrigin });

  mainWindow.loadURL(`${getTrustedOrigin()}/`);

  // Open DevTools in development
  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Cleanup on quit
app.on('before-quit', (event) => {
  if (quitAllowed) return;
  event.preventDefault();
  quitting = true;
  backendReady = false;
  startup.abort();
  ipcMain.removeHandler(IPC_CHANNELS.OPEN_EXTERNAL);
  ipcMain.removeHandler(IPC_CHANNELS.NOTIFY);
  if (!shutdown) {
    shutdown = stopBackend(djangoProcess)
      .catch((error) => console.error('Backend tree shutdown failed:', error))
      .finally(() => {
        quitAllowed = true;
        app.quit();
      });
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (!quitting && backendReady && mainWindow === null) {
    createWindow();
  }
});
