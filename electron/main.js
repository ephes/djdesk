const { app, BrowserWindow } = require('electron');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const http = require('http');

const DJANGO_BUNDLE_DIR = path.join(__dirname, 'django-bundle');
const BUNDLE_RUNNER = path.join(DJANGO_BUNDLE_DIR, 'run_django.py');

let mainWindow;
let djangoProcess;
let djangoPort;
let getPortModule;

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
    if (!getPortModule) {
      ({ default: getPortModule } = await import('get-port'));
    }

    // Find Python interpreter and determine run mode
    const pythonInfo = resolvePython();
    const isBundled = pythonInfo.mode === 'bundled';
    console.log(
      `Using ${isBundled ? 'bundled' : 'system'} Python: ${pythonInfo.interpreter}`
    );

    // Get an available port
    djangoPort = await getPortModule({ port: 8000 });
    console.log(`Starting Django on port ${djangoPort}...`);

    // Path to manage.py (one level up from electron/)
    const managePyPath = path.join(__dirname, '..', 'manage.py');

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
      DJANGO_SETTINGS_MODULE: process.env.DJANGO_SETTINGS_MODULE || 'djdesk.settings.local',
      PYTHONHOME: isBundled ? path.join(DJANGO_BUNDLE_DIR, 'python') : process.env.PYTHONHOME
    };

    // Spawn Django dev server
    djangoProcess = spawn(pythonInfo.interpreter, djangoArgs, {
      cwd: djangoCwd,
      env: djangoEnv
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

    djangoProcess.on('close', (code) => {
      console.log(`Django process exited with code ${code}`);
      if (code !== 0 && code !== null) {
        app.quit();
      }
    });

    // Wait for Django to be ready
    await waitForDjango();

    // Create the Electron window
    createWindow();
  } catch (error) {
    console.error('Error starting Django:', error);
    killDjangoAndQuit();
  }
}

/**
 * Kill Django process and quit the app
 * Used for startup failures before app is fully ready
 */
function killDjangoAndQuit() {
  if (djangoProcess) {
    console.log('Terminating Django process...');
    djangoProcess.kill('SIGTERM');
  }
  app.quit();
}

function waitForDjango() {
  return new Promise((resolve, reject) => {
    const maxAttempts = 30;
    let attempts = 0;

    const checkServer = () => {
      attempts++;

      const req = http.get(`http://127.0.0.1:${djangoPort}/`, (res) => {
        // Consume the response to free the socket
        res.resume();
        console.log(`Django server responded with status: ${res.statusCode}`);
        resolve();
      });

      // Add timeout to prevent hanging
      req.setTimeout(2000, () => {
        req.destroy();
        if (attempts >= maxAttempts) {
          reject(new Error('Django server failed to start (timeout)'));
        } else {
          setTimeout(checkServer, 500);
        }
      });

      req.on('error', (err) => {
        if (attempts >= maxAttempts) {
          reject(new Error('Django server failed to start'));
        } else {
          setTimeout(checkServer, 500);
        }
      });

      req.end();
    };

    // Give Django a moment to start before first check
    setTimeout(checkServer, 1000);
  });
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

  mainWindow.loadURL(`http://127.0.0.1:${djangoPort}/`);

  // Open DevTools in development
  if (process.env.NODE_ENV === 'development') {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// Cleanup on quit
app.on('before-quit', () => {
  if (djangoProcess) {
    console.log('Shutting down Django server...');
    djangoProcess.kill('SIGTERM');
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
