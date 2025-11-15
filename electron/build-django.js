#!/usr/bin/env node

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { spawn, spawnSync, execSync } = require('child_process');

const projectRoot = path.resolve(__dirname, '..');
const bundleRoot = path.join(__dirname, 'django-bundle');
const pythonDir = path.join(bundleRoot, 'python');
const bundleSrcDir = path.join(bundleRoot, 'src');
const djangoSourceDir = path.join(projectRoot, 'src', 'djdesk');
const managePy = path.join(projectRoot, 'manage.py');

function buildPythonPath(...paths) {
  const entries = [...paths];
  if (process.env.PYTHONPATH) {
    entries.push(process.env.PYTHONPATH);
  }
  return entries.filter(Boolean).join(path.delimiter);
}

async function main() {
  console.log('Building Django bundle (Phase 2)...');
  await recreateBundle();

  const systemPython = findSystemPython();
  console.log(`Using system Python: ${systemPython}`);
  await createVirtualEnv(systemPython);

  const bundlePython = getBundlePython();
  await installPythonDeps(bundlePython);
  await copyProjectFiles();
  await collectStatic(bundlePython);
  await writeLauncherScript();
  await writeVersionFile();
  await verifyBundle(bundlePython);

  console.log('django-bundle ready.');
}

async function recreateBundle() {
  await fsp.rm(bundleRoot, { recursive: true, force: true });
  await fsp.mkdir(bundleRoot, { recursive: true });
}

function findSystemPython() {
  const explicit = process.env.PYTHON;
  if (explicit && commandExists(explicit)) {
    return explicit;
  }

  const candidates = ['python3.14', 'python3', 'python'];
  for (const candidate of candidates) {
    if (commandExists(candidate)) {
      return candidate;
    }
  }

  throw new Error(
    'Unable to find a Python 3.14+ interpreter. Set PYTHON or adjust your PATH.'
  );
}

function commandExists(command) {
  try {
    spawnSync(command, ['--version'], { stdio: 'ignore' });
    return true;
  } catch (error) {
    return false;
  }
}

async function createVirtualEnv(pythonCmd) {
  console.log('Creating virtual environment inside django-bundle...');
  await runCommand(pythonCmd, ['-m', 'venv', pythonDir]);
}

function getBundlePython() {
  const pythonExecutable = process.platform === 'win32'
    ? path.join(pythonDir, 'Scripts', 'python.exe')
    : path.join(pythonDir, 'bin', 'python');

  if (!fs.existsSync(pythonExecutable)) {
    throw new Error(`Virtualenv python missing at ${pythonExecutable}`);
  }

  return pythonExecutable;
}

async function installPythonDeps(pythonExecutable) {
  console.log('Installing Django dependencies via uv...');
  await runCommand('uv', [
    'pip',
    'install',
    '--python',
    pythonExecutable,
    '-r',
    path.join(projectRoot, 'pyproject.toml')
  ], {
    cwd: projectRoot
  });
}

async function copyProjectFiles() {
  console.log('Copying Django sources into django-bundle/src ...');
  await fsp.mkdir(bundleSrcDir, { recursive: true });
  await fsp.cp(djangoSourceDir, path.join(bundleSrcDir, 'djdesk'), {
    recursive: true,
    filter: (source) => !source.includes('__pycache__')
  });
  await fsp.copyFile(managePy, path.join(bundleRoot, 'manage.py'));
}

async function collectStatic(pythonExecutable) {
  console.log('Running collectstatic targeting the bundle...');
  const staticRoot = path.join(bundleRoot, 'staticfiles');
  const pythonPathEntries = [path.join(projectRoot, 'src')];
  if (process.env.PYTHONPATH) {
    pythonPathEntries.push(process.env.PYTHONPATH);
  }
  const env = {
    ...process.env,
    DJANGO_STATIC_ROOT: staticRoot,
    DJANGO_SETTINGS_MODULE: process.env.DJANGO_SETTINGS_MODULE || 'djdesk.settings.local',
    PYTHONPATH: buildPythonPath(path.join(projectRoot, 'src'))
  };
  await runCommand(pythonExecutable, [
    managePy,
    'collectstatic',
    '--no-input',
    '--clear'
  ], {
    cwd: projectRoot,
    env
  });
}

async function writeLauncherScript() {
  console.log('Writing bundle launcher script...');
  const launcherPath = path.join(bundleRoot, 'run_django.py');
  const content = String.raw`#!/usr/bin/env python

"""Launch Django from the bundled sources."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _augment_pythonpath(src_root: Path) -> None:
    existing = os.environ.get("PYTHONPATH")
    if existing:
        os.environ["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}"
    else:
        os.environ["PYTHONPATH"] = str(src_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bundled Django server")
    parser.add_argument("--port", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    bundle_root = Path(__file__).resolve().parent
    src_root = bundle_root / "src"
    if src_root.exists():
        _augment_pythonpath(src_root)
        sys.path.insert(0, str(src_root))

    os.environ.setdefault("DJANGO_ENV", "local")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djdesk.settings.local")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - surfaced via Electron logs
        raise SystemExit(f"Failed to import Django: {exc}")

    host = args.host
    port = args.port
    command = ["manage.py", "runserver", f"{host}:{port}", "--noreload"]
    sys.argv = command
    os.chdir(bundle_root)
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
`;
  await fsp.writeFile(launcherPath, content, 'utf8');
  await fsp.chmod(launcherPath, 0o755);
}

async function writeVersionFile() {
  const versionPath = path.join(bundleRoot, 'VERSION');
  let version = 'unknown';
  try {
    version = execSync('git rev-parse --short HEAD', {
      cwd: projectRoot,
      stdio: ['ignore', 'pipe', 'ignore']
    }).toString().trim();
  } catch (error) {
    // ignore, keep default
  }

  const payload = `${version}\nBuilt: ${new Date().toISOString()}\n`;
  await fsp.writeFile(versionPath, payload, 'utf8');
}

async function verifyBundle(pythonExecutable) {
  console.log('Verifying bundle Python environment...');
  const env = {
    ...process.env,
    PYTHONPATH: buildPythonPath(path.join(bundleRoot, 'src'))
  };
  const result = spawnSync(pythonExecutable, [
    '-c',
    'import django, sys; sys.stdout.write(django.get_version())'
  ], {
    cwd: bundleRoot,
    env,
    encoding: 'utf8',
    stdio: 'pipe'
  });

  if (result.status !== 0) {
    const stderr = result.stderr ? result.stderr.trim() : '';
    const stdout = result.stdout ? result.stdout.trim() : '';
    throw new Error(
      `Django verification failed (status ${result.status}): ${stderr || stdout}`
    );
  }

  console.log(`Verified Django ${result.stdout.trim()} inside bundle.`);
}

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: 'inherit',
      shell: false,
      ...options
    });

    child.on('error', (error) => {
      reject(error);
    });

    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${command} exited with status ${code}`));
      }
    });
  });
}

main().catch((error) => {
  console.error('\nFailed to build django-bundle:', error.message);
  process.exit(1);
});
