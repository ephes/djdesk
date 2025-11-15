const { app, BrowserWindow } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');

let mainWindow;
let djangoProcess;
let djangoPort;
let getPortModule;

/**
 * Find a suitable Python interpreter
 * Respects PYTHON env var, then tries python3, then python
 */
function findPython() {
  if (process.env.PYTHON) {
    return process.env.PYTHON;
  }

  // Try python3 first (common on macOS/Linux)
  const candidates = ['python3', 'python'];
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

    // Find Python interpreter
    const pythonCmd = findPython();
    console.log(`Using Python: ${pythonCmd}`);

    // Get an available port
    djangoPort = await getPortModule({ port: 8000 });
    console.log(`Starting Django on port ${djangoPort}...`);

    // Path to manage.py (one level up from electron/)
    const managePyPath = path.join(__dirname, '..', 'manage.py');

    // Spawn Django dev server
    djangoProcess = spawn(pythonCmd, [
      managePyPath,
      'runserver',
      `127.0.0.1:${djangoPort}`,
      '--noreload'
    ], {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, DJANGO_ENV: 'local' }
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
