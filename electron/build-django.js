#!/usr/bin/env node

const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const toml = require('toml');
const { spawn, spawnSync, execSync } = require('child_process');

const projectRoot = path.resolve(__dirname, '..');
const bundleRoot = path.join(__dirname, 'django-bundle');
const pythonDir = path.join(bundleRoot, 'python');
const bundleSrcDir = path.join(bundleRoot, 'src');
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

async function main() {
  console.log('Building Django bundle (Phase 3)...');
  await recreateBundle();

  const standaloneSpec = resolveStandaloneSpec();
  const bundlePython = await prepareStandalonePython(standaloneSpec);
 
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

async function prepareStandalonePython({ assetName, pythonRelative }) {
  console.log('Preparing python-build-standalone interpreter...');
  ensureTarAvailable();
  const downloadDir = path.join(__dirname, '.python-downloads');
  await fsp.mkdir(downloadDir, { recursive: true });
  const archivePath = path.join(downloadDir, assetName);
  const downloadUrl = `${PYTHON_DOWNLOAD_BASE}/${PYTHON_BUILD_RELEASE}/${assetName}`;

  if (!fs.existsSync(archivePath)) {
    console.log(`Downloading ${assetName}...`);
    await downloadFile(downloadUrl, archivePath);
  } else {
    console.log(`Reusing cached interpreter archive ${archivePath}`);
  }

  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'djdesk-python-'));
  await verifyChecksum(archivePath, assetName);

  try {
    await extractArchive(archivePath, tempDir);

    const extractedPythonDir = path.join(tempDir, 'python');
    if (!fs.existsSync(extractedPythonDir)) {
      throw new Error('python-build-standalone archive missing python directory');
    }

    await fsp.rm(pythonDir, { recursive: true, force: true });
    await fsp.mkdir(path.dirname(pythonDir), { recursive: true });
    try {
      await fsp.rename(extractedPythonDir, pythonDir);
    } catch (error) {
      if (error.code !== 'EXDEV') {
        throw error;
      }
      await fsp.cp(extractedPythonDir, pythonDir, { recursive: true, dereference: false });
    }
  } finally {
    await fsp.rm(tempDir, { recursive: true, force: true });
  }

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

async function downloadFile(url, destination) {
  await fsp.mkdir(path.dirname(destination), { recursive: true });
  return new Promise((resolve, reject) => {
    const request = https.get(url, (response) => {
      if (response.statusCode && response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.destroy();
        downloadFile(response.headers.location, destination).then(resolve).catch(reject);
        return;
      }

      if (response.statusCode !== 200) {
        reject(new Error(`Failed to download ${url} (status ${response.statusCode})`));
        return;
      }

      const file = fs.createWriteStream(destination);
      response.pipe(file);
      file.on('finish', () => {
        file.close(resolve);
      });
      file.on('error', reject);
    });

    request.on('error', reject);
  });
}

async function extractArchive(archivePath, destination) {
  await fsp.mkdir(destination, { recursive: true });
  const args = ['-xzf', archivePath, '-C', destination];
  if (process.platform === 'win32') {
    args.splice(1, 0, '--force-local');
  }
  await runCommand('tar', args);
}

function ensureTarAvailable() {
  try {
    execSync('tar --version', { stdio: 'ignore' });
  } catch (error) {
    throw new Error('tar command not found. Please install tar (available on modern macOS/Linux and Windows 10+) before bundling.');
  }
}

async function verifyChecksum(filePath, assetName) {
  const expected = PYTHON_SHA256[assetName];
  if (!expected) {
    throw new Error(`No checksum registered for ${assetName}`);
  }

  const hash = crypto.createHash('sha256');
  await new Promise((resolve, reject) => {
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('end', resolve);
    stream.on('error', reject);
  });
  const actual = hash.digest('hex');
  if (actual !== expected) {
    throw new Error(`Checksum mismatch for ${assetName}. Expected ${expected}, got ${actual}`);
  }
}

function ensurePythonVersion(pythonExecutable) {
  const result = spawnSync(pythonExecutable, ['--version'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe']
  });
  const output = `${result.stdout}${result.stderr}`.trim();
  if (!output.includes(PYTHON_VERSION)) {
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
