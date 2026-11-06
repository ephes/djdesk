# DJDesk Electron App

This directory contains the Electron desktop application wrapper for DJDesk.

## Current Implementation

DJDesk uses **Phase 3 — bundled python-build-standalone**:

- `electron/django-bundle/` contains a standalone Python interpreter, runtime dependencies, collected static files, and the project sources. `npm start` prefers this bundle.
- System Python remains the development fallback when no bundle exists; it needs the backend dependencies installed separately.

## Prerequisites

1. **Node.js >=22.12.0**, matching `package.json` engines.
2. **[uv](https://github.com/astral-sh/uv)** for backend development and documentation dependencies. Bundling installs runtime dependencies with the downloaded interpreter's pip.
3. **tar** available in your shell. (This is mandatory—`build-django.js` shells out to `tar` to unpack python-build-standalone archives. Windows 10+ ships `tar` by default; older versions need an external install.)

> The Phase 3 bundle **downloads its own Python 3.14 interpreter** via
> [`python-build-standalone`](https://github.com/indygreg/python-build-standalone). You only need a system Python when running
> the fallback Phase 1 shell without a bundle.

## Installing Node Dependencies

```bash
cd electron
npm install
```

## Building the Django Bundle (Phase 3)

```bash
# from the repo root
just electron-bundle
# or
cd electron && npm run bundle
```

`build-django.js` performs the following steps:

1. Creates a fresh `django-bundle/` directory.
2. Downloads the appropriate **python-build-standalone Python 3.14.0+20251031 interpreter** for your platform and unpacks it into `django-bundle/python/` (verifying SHA256 checksums before extraction).
3. Installs Django dependencies into that interpreter via `pip install` using the dependencies declared in `pyproject.toml`.
4. Copies `manage.py` into `django-bundle/` and `src/djdesk` into `django-bundle/src/`.
5. Runs `collectstatic --clear` with `DJANGO_STATIC_ROOT` pointed at `django-bundle/staticfiles`.
6. Writes `run_django.py` (the launcher Electron calls) and a `VERSION` file with the current Git SHA.
7. Verifies the bundled interpreter by importing Django before exiting.

> Only the packages listed under `[project].dependencies` in `pyproject.toml` are installed. Optional extras/dev dependencies are ignored.

The bundle is ignored by Git and may be regenerated at any time.

## Shell Boundary and Build Tests

`boundary-guards.js` keeps the window on the local Django origin, denies popups and `<webview>` tags, validates IPC sender frames, and limits `shell.openExternal` to `http(s)` URLs. See "Shell boundary" in `docs/architecture.rst`.

```bash
cd electron
npm ci --omit=dev --ignore-scripts  # runtime modules only; does not install Electron
npm test                          # node --test test/*.test.js
```

The build-contract tests import `build-django.js` without running its build entrypoint.
They generate a launcher in a temporary bundle and execute it with a synthetic Django
management module, checking migrations before serving and failure propagation. Other
tests read invented TOML projects and replace subprocesses to check pip/collectstatic
arguments, bundle paths, interpreter versions, and Django import failures. They use an
existing Python 3 from `PYTHON` or PATH; no pip installation, interpreter download,
Electron launch, real service or account is used. These tests run in normal PR CI.
`just electron-test` runs these tests from the repo root, reusing installed modules
and bootstrapping runtime modules only when TOML is missing. An existing full
development install is preserved, and repeat test runs need no registry access.
After a runtime-only install, run `npm install` to restore development dependencies
before launching or packaging Electron.

## Running the App

```bash
cd electron
npm start
```

- If `django-bundle/python` exists, Electron uses it to run `run_django.py --host 127.0.0.1 --port <random>`.
- If the bundle is missing, we fall back to the Phase 1 behavior and spawn the system Python.
- Close the window or press `Ctrl+C` to shut down both Django and Electron.

You can still override the interpreter with `PYTHON=/path/to/python npm start`, but the bundled interpreter wins when present so you rarely need to.

## Packaging (experimental)

`npm run build` runs `npm run bundle` and then calls `electron-builder` using `electron/electron-builder.json`. The builder copies `django-bundle/` (including the python-build-standalone interpreter) via `extraResources`, so installers contain a fully offline runtime. Cross-platform CI is handled by `.github/workflows/electron-desktop.yml`.

### Local OS-specific builds

The `just` file contains helpers that mirror the commands we run in CI. Execute them on the matching host OS:

```bash
just electron-build-macos
just electron-build-linux
just electron-build-windows
# or rely on auto-detection
just electron-build
```

Under the hood these commands run `npm run build -- --<platform>` to produce assets under `electron/dist/`.

### GitHub Actions workflow

`.github/workflows/electron-desktop.yml` builds macOS, Windows, and Linux artifacts in parallel. Each job:

1. Runs `npm ci` inside `electron/`.
2. Executes `npm run bundle` to generate the Django payload.
3. Calls `npm run build -- --<platform>` so Electron Builder packages the bundle.
4. Uploads the resulting `dist/` directory as a workflow artifact.

## Bundle Layout

```
electron/django-bundle/
├── manage.py
├── run_django.py
├── src/djdesk/...           # copied Django project
├── staticfiles/             # output of collectstatic
├── python/                  # python-build-standalone Python 3.14 interpreter with Django deps
└── VERSION                  # git SHA + timestamp for cache busting
```

## Troubleshooting

**"build-django.js failed"**
- Ensure your shell can run `tar` (needed to extract python-build-standalone).
- Confirm you have network access to GitHub to download the interpreter artifacts.
- Delete `electron/django-bundle/` and try again if permissions look odd.

**"Django server failed to start"**
- Confirm the bundle exists and contains `python/bin/python3` (macOS/Linux) or `python/python.exe` (Windows).
- Check the terminal logs; `run_django.py` bubbles Django errors directly to stdout/stderr.
- Delete the bundle and rebuild if dependencies look stale.

**Port conflicts**
- The app uses `get-port` to find a free port, but another process may still win the race. Re-run `npm start` if the selected port was in use.

**Window doesn't open**
- Review the console output for Django tracebacks.
- The bootstrapper waits ~15 seconds for the Django HTTP endpoint before giving up; misconfigured settings will surface there.
