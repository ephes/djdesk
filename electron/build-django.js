#!/usr/bin/env node

const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');
const { downloadPython, checksum, snapshotPythonArchive } = require('./python-download');
const toml = require('toml');
const { spawn, spawnSync, execSync } = require('child_process');

const projectRoot = path.resolve(__dirname, '..');
const bundleRoot = path.join(__dirname, 'django-bundle');
const djangoSourceDir = path.join(projectRoot, 'src', 'djdesk');
const managePy = path.join(projectRoot, 'manage.py');

const PYTHON_VERSION = '3.14.0';
const PYTHON_BUILD_RELEASE = '20251031';
const PYTHON_DOWNLOAD_BASE = 'https://github.com/indygreg/python-build-standalone/releases/download';
const PYTHON_SHA256 = {
  'cpython-3.14.0+20251031-aarch64-apple-darwin-install_only.tar.gz':
    'b4bcd3c6c24cab32ae99e1b05c89312b783b4d69431d702e5012fe1fdcad4087',
  'cpython-3.14.0+20251031-x86_64-apple-darwin-install_only.tar.gz':
    '4e71a3ce973be377ef18637826648bb936e2f9490f64a9e4f33a49bcc431d344',
  'cpython-3.14.0+20251031-x86_64-unknown-linux-gnu-install_only.tar.gz':
    '3dec1ab70758a3467ac3313bbcdabf7a9b3016db5c072c4537e3cf0a9e6290f6',
  'cpython-3.14.0+20251031-aarch64-unknown-linux-gnu-install_only.tar.gz':
    '128a9cbfb9645d5237ec01704d9d1d2ac5f084464cc43c37a4cd96aa9c3b1ad5',
  'cpython-3.14.0+20251031-x86_64-pc-windows-msvc-install_only.tar.gz':
    '39acfcb3857d83eab054a3de11756ffc16b3d49c31393b9800dd2704d1f07fdf',
  'cpython-3.14.0+20251031-aarch64-pc-windows-msvc-install_only.tar.gz':
    '599a8b7e12439cd95a201dbdfe95cf363146b1ff91f379555dafd86b170caab9'
};
const PLATFORM_MATRIX = {
  darwin: {
    arch: {
      arm64: { triple: 'aarch64-apple-darwin', pythonRelative: ['bin', 'python3'] },
      x64: { triple: 'x86_64-apple-darwin', pythonRelative: ['bin', 'python3'] }
    }
  },
  linux: {
    arch: {
      x64: { triple: 'x86_64-unknown-linux-gnu', pythonRelative: ['bin', 'python3'] },
      arm64: { triple: 'aarch64-unknown-linux-gnu', pythonRelative: ['bin', 'python3'] }
    }
  },
  win32: {
    arch: {
      x64: { triple: 'x86_64-pc-windows-msvc', pythonRelative: ['python.exe'] },
      arm64: { triple: 'aarch64-pc-windows-msvc', pythonRelative: ['python.exe'] }
    }
  }
};

async function main({ destination = bundleRoot, steps = {}, io = fsp } = {}) {
  const operations = {
    prepareStandalonePython, installPythonDeps, copyProjectFiles, collectStatic,
    writeLauncherScript, writeVersionFile, verifyBundle, ...steps
  };
  const parent = await io.realpath(path.dirname(destination));
  destination = path.join(parent, path.basename(destination));
  const lock = `${destination}.lock`;
  try {
    await io.mkdir(lock);
  } catch (error) {
    if (error.code === 'EEXIST') throw new Error(`Runtime already building; reservation: ${lock}`);
    throw error;
  }
  let failure;
  try {
    await publishRuntime(destination, operations, io);
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      await io.rmdir(lock);
    } catch (error) {
      if (!failure) throw error;
      failure.message += `; reservation cleanup failed: ${error.message}`;
    }
  }
}

async function publishRuntime(destination, operations, io) {
  const parent = path.dirname(destination);
  const work = await io.mkdtemp(path.join(parent, '.django-bundle-publish-'));
  const staging = path.join(work, 'staging');
  const backup = path.join(work, 'backup');
  let retained = false;
  let failure;
  try {
    await io.mkdir(staging);
    await buildRuntime(staging, operations);
    const candidate = await io.lstat(staging, { bigint: true });
    const previous = await runtimeIdentity(destination, io);
    if (previous) {
      try {
        await io.rename(destination, backup);
      } catch (error) {
        retained = await reconcileRuntime({ previous, destination, backup, error, io }) === 'retained';
        throw error;
      }
    }
    try {
      await io.rename(staging, destination);
    } catch (error) {
      const outcome = await reconcileRuntime({ previous, destination, backup, error, io, candidate });
      if (outcome === 'installed') return;
      retained = outcome === 'retained';
      throw error;
    }
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    if (!retained) {
      try {
        await io.rm(work, { recursive: true, force: true });
      } catch (error) {
        if (!failure) throw error;
        failure.message += `; staging cleanup failed: ${error.message}`;
      }
    }
  }
}

async function runtimeIdentity(file, io) {
  try {
    return await io.lstat(file, { bigint: true });
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function sameRuntime(expected, actual) {
  return actual && expected.dev === actual.dev && expected.ino === actual.ino;
}

async function reconcileRuntime({ previous, destination, backup, error, io, candidate }) {
  try {
    const installed = await runtimeIdentity(destination, io);
    if (candidate && sameRuntime(candidate, installed)) return 'installed';
    if (!previous) return 'failed';
    const saved = await runtimeIdentity(backup, io);
    if (!saved && sameRuntime(previous, installed)) return 'restored';
    if (installed || !sameRuntime(previous, saved)) {
      throw new Error('runtime generation could not be safely restored');
    }
    await io.rename(backup, destination);
    return 'restored';
  } catch (restoreError) {
    error.recoveryPath = previous ? backup : path.dirname(backup);
    error.message += `; restoration failed: ${restoreError.message}; inspect ${error.recoveryPath}`;
    return 'retained';
  }
}

async function buildRuntime(destination, operations) {
  const python = await operations.prepareStandalonePython(resolveStandaloneSpec(), destination);
  await operations.installPythonDeps(python);
  await operations.copyProjectFiles(destination);
  await operations.collectStatic(python, destination);
  await operations.writeLauncherScript(destination);
  await operations.writeVersionFile(destination);
  await operations.verifyBundle(python, destination);
}

function resolveStandaloneSpec() {
  const platformConfig = PLATFORM_MATRIX[process.platform];
  if (!platformConfig) {
    throw new Error(`Unsupported platform: ${process.platform}`);
  }

  const archConfig = platformConfig.arch[process.arch];
  if (!archConfig) {
    throw new Error(`Unsupported architecture ${process.arch} on ${process.platform}`);
  }

  const assetName = `cpython-${PYTHON_VERSION}+${PYTHON_BUILD_RELEASE}-${archConfig.triple}-install_only.tar.gz`;
  return {
    assetName,
    pythonRelative: archConfig.pythonRelative
  };
}

async function prepareStandalonePython({ assetName, pythonRelative }, root = bundleRoot) {
  const pythonDir = path.join(root, 'python');
  console.log('Preparing python-build-standalone interpreter...');
  ensureTarAvailable();
  const downloadDir = path.join(__dirname, '.python-downloads');
  await fsp.mkdir(downloadDir, { recursive: true });
  const archivePath = path.join(downloadDir, assetName);
  const downloadUrl = `${PYTHON_DOWNLOAD_BASE}/${PYTHON_BUILD_RELEASE}/${assetName}`;

  await downloadPython(downloadUrl, archivePath, PYTHON_SHA256[assetName]);

  await extractPython(archivePath, assetName, pythonDir);

  const pythonExecutable = path.join(pythonDir, ...pythonRelative);
  if (!fs.existsSync(pythonExecutable)) {
    throw new Error(`Python executable not found at ${pythonExecutable}`);
  }

  if (process.platform !== 'win32') {
    await fsp.chmod(pythonExecutable, 0o755);
  }

  ensurePythonVersion(pythonExecutable);
  console.log(`Standalone interpreter ready at ${pythonExecutable}`);
  return pythonExecutable;
}

async function extractPython(archivePath, assetName, pythonDir, {
  verify = verifyChecksum, extract = extractArchive, io = fsp
} = {}) {
  const tempDir = await io.mkdtemp(path.join(os.tmpdir(), 'djdesk-python-'));
  let failure;
  try {
    const snapshot = path.join(tempDir, 'archive.tar.gz');
    const unpacked = path.join(tempDir, 'unpacked');
    const limits = { maxBytes: 256 * 1024 * 1024, signal: AbortSignal.timeout(120000) };
    await snapshotPythonArchive(archivePath, snapshot, limits);
    await verify(snapshot, assetName, limits);
    await extract(snapshot, unpacked);

    const extractedPythonDir = path.join(unpacked, 'python');
    if (!fs.existsSync(extractedPythonDir)) {
      throw new Error('python-build-standalone archive missing python directory');
    }

    await io.rm(pythonDir, { recursive: true, force: true });
    await io.mkdir(path.dirname(pythonDir), { recursive: true });
    try {
      await io.rename(extractedPythonDir, pythonDir);
    } catch (error) {
      if (error.code !== 'EXDEV') {
        throw error;
      }
      await io.cp(extractedPythonDir, pythonDir, { recursive: true, dereference: false });
    }
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      await io.rm(tempDir, { recursive: true, force: true });
    } catch (error) {
      if (!failure) throw error;
      failure.message += `; extraction cleanup failed (${tempDir}): ${error.message}`;
    }
  }
}

async function extractArchive(archivePath, destination) {
  await fsp.mkdir(destination, { recursive: true });
  const args = ['-xzf', archivePath, '-C', destination];
  await runCommand('tar', args);
}

function ensureTarAvailable() {
  try {
    execSync('tar --version', { stdio: 'ignore' });
  } catch (error) {
    throw new Error('tar command not found. Please install tar (available on modern macOS/Linux and Windows 10+) before bundling.');
  }
}

async function verifyChecksum(filePath, assetName, limits) {
  const expected = PYTHON_SHA256[assetName];
  if (!expected) {
    throw new Error(`No checksum registered for ${assetName}`);
  }

  if (!await checksum(filePath, expected, limits)) {
    throw new Error(`Checksum mismatch for ${assetName}`);
  }
}

function ensurePythonVersion(pythonExecutable) {
  const result = spawnSync(pythonExecutable, ['--version'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 10000,
    killSignal: 'SIGKILL'
  });
  if (result.error) {
    throw new Error(`Python version probe failed: ${result.error.message}`, { cause: result.error });
  }
  const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`.trim();
  if (result.status !== 0) {
    const exit = result.signal ? `signal ${result.signal}` : `status ${result.status}`;
    throw new Error(`Python version probe failed (${exit}): ${output}`);
  }
  const version = /^Python[ \t]+(\S+)$/.exec(output)?.[1];
  if (version !== PYTHON_VERSION) {
    throw new Error(`Unexpected Python version (${output}); expected ${PYTHON_VERSION}`);
  }
}

function buildPythonPath(...paths) {
  const entries = [...paths];
  if (process.env.PYTHONPATH) {
    entries.push(process.env.PYTHONPATH);
  }
  return entries.filter(Boolean).join(path.delimiter);
}

function readProjectDependencies() {
  const pyprojectPath = path.join(projectRoot, 'pyproject.toml');
  const tomlContent = fs.readFileSync(pyprojectPath, 'utf8');
  const data = toml.parse(tomlContent);
  return data.project?.dependencies ?? [];
}

async function installPythonDeps(pythonExecutable) {
  const dependencies = readProjectDependencies();
  if (dependencies.length === 0) {
    console.log('No runtime dependencies declared in pyproject.toml.');
    return;
  }

  console.log('Installing Django dependencies via pip...');
  const pipEnv = {
    ...process.env,
    PIP_REQUIRE_VIRTUALENV: '0'
  };
  await runCommand(pythonExecutable, [
    '-m',
    'pip',
    'install',
    '--no-cache-dir',
    ...dependencies
  ], {
    cwd: projectRoot,
    env: pipEnv
  });
}

async function copyProjectFiles(root = bundleRoot) {
  const bundleSrcDir = path.join(root, 'src');
  console.log('Copying Django sources into django-bundle/src ...');
  await fsp.mkdir(bundleSrcDir, { recursive: true });
  await fsp.cp(djangoSourceDir, path.join(bundleSrcDir, 'djdesk'), {
    recursive: true,
    filter: (source) => !source.includes('__pycache__')
  });
  await fsp.copyFile(managePy, path.join(root, 'manage.py'));
}

async function collectStatic(pythonExecutable, root = bundleRoot) {
  console.log('Running collectstatic targeting the bundle...');
  const staticRoot = path.join(root, 'staticfiles');
  const env = {
    ...process.env,
    DJANGO_STATIC_ROOT: staticRoot,
    DJANGO_ENV: process.env.DJANGO_ENV || 'local',
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

async function writeLauncherScript(root = bundleRoot) {
  console.log('Writing bundle launcher script...');
  const launcherPath = path.join(root, 'run_django.py');
const content = String.raw`#!/usr/bin/env python

"""Launch Django from the bundled sources."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


def _augment_pythonpath(src_root: Path) -> None:
    existing = os.environ.get("PYTHONPATH")
    if existing:
        os.environ["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}"
    else:
        os.environ["PYTHONPATH"] = str(src_root)


def _run_management_command(execute, command: Sequence[str]) -> None:
    sys.argv = list(command)
    execute(sys.argv)


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

    os.chdir(bundle_root)

    migrate_command = ["manage.py", "migrate", "--noinput"]
    _run_management_command(execute_from_command_line, migrate_command)

    host = args.host
    port = args.port
    runserver_command = ["manage.py", "runserver", f"{host}:{port}", "--noreload"]
    _run_management_command(execute_from_command_line, runserver_command)


if __name__ == "__main__":
    main()
`;
  await fsp.writeFile(launcherPath, content, 'utf8');
  await fsp.chmod(launcherPath, 0o755);
}

async function writeVersionFile(root = bundleRoot) {
  const versionPath = path.join(root, 'VERSION');
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

async function verifyBundle(pythonExecutable, root = bundleRoot) {
  console.log('Verifying bundle Python environment...');
  const env = {
    ...process.env,
    PYTHONPATH: buildPythonPath(path.join(root, 'src'))
  };
  const result = spawnSync(pythonExecutable, [
    '-c',
    'import django, sys; sys.stdout.write(django.get_version())'
  ], {
    cwd: root,
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

module.exports = {
  main,
  extractPython,
  writeLauncherScript,
  readProjectDependencies,
  installPythonDeps,
  collectStatic,
  ensurePythonVersion,
  verifyBundle
};

if (require.main === module) {
  main().catch((error) => {
    console.error('\nFailed to build django-bundle:', error.message);
    process.exit(1);
  });
}
