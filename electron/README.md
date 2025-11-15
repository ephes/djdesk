# DJDesk Electron App

This directory contains the Electron desktop application wrapper for DJDesk.

## Current Implementation

DJDesk has progressed to **Phase 2 — Option B (bundled virtualenv)**:

- Phase 1 remains available as the fallback: when no bundle exists we spawn your system Python and run `manage.py runserver` exactly as before.
- Phase 2 adds `electron/django-bundle/`, a self-contained virtual environment that includes Django, collected static files, and the project source code. `npm start` now prefers this bundle so the Electron shell can run even without globally installed dependencies.

Phase 3 (fully relocatable python-build-standalone interpreters) will build on top of this structure.

## Prerequisites

1. **Python 3.14+** accessible via `python3.14`, `python3`, or `python`.
2. **[uv](https://github.com/astral-sh/uv)** installed on your PATH (the bundler relies on `uv pip install`).
3. **DJDesk dependencies** installed once via `uv sync` (or `uv pip install -e .`).
4. **Node.js v18+**.

## Installing Node Dependencies

```bash
cd electron
npm install
```

## Building the Django Bundle (Phase 2)

```bash
# from the repo root
just electron-bundle
# or
cd electron && npm run bundle
```

`build-django.js` performs the following steps:

1. Creates a fresh `django-bundle/` directory.
2. Creates a virtualenv inside `django-bundle/python/` using your Python 3.14 interpreter.
3. Installs Django dependencies via `uv pip install` into that virtualenv.
4. Copies `manage.py` and `src/djdesk` into `django-bundle/src/`.
5. Runs `collectstatic --clear` with `DJANGO_STATIC_ROOT` pointed at `django-bundle/staticfiles`.
6. Writes `run_django.py` (the launcher Electron calls) and a `VERSION` file with the current Git SHA.
7. Verifies the bundled interpreter by importing Django before exiting.

The bundle is ignored by Git and may be regenerated at any time.

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

`npm run build` runs `npm run bundle` and then calls `electron-builder` using `electron/electron-builder.json`. The builder copies `django-bundle/` via `extraResources`, so future installers automatically include the Python virtualenv. Cross-platform CI is deferred to Phase 3.

### Local OS-specific builds

The `just` file contains helpers that mirror the commands we run in CI. Execute them on the matching host OS:

```bash
just electron-build-macos
just electron-build-linux
just electron-build-windows
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
├── python/                  # virtualenv with Python 3.14 + deps
└── VERSION                  # git SHA + timestamp for cache busting
```

## Troubleshooting

**"build-django.js failed"**
- Ensure `python3.14 --version` works (the script tries `python3.14`, then `python3`, then `python`).
- Ensure `uv --version` works; install uv if missing.
- Delete `electron/django-bundle/` and try again if permissions look odd.

**"Django server failed to start"**
- Confirm the bundle exists and contains `python/bin/python` (macOS/Linux) or `python/Scripts/python.exe` (Windows).
- Check the terminal logs; `run_django.py` bubbles Django errors directly to stdout/stderr.
- Delete the bundle and rebuild if dependencies look stale.

**Port conflicts**
- The app uses `get-port` to find a free port, but another process may still win the race. Re-run `npm start` if the selected port was in use.

**Window doesn't open**
- Review the console output for Django tracebacks.
- The bootstrapper waits ~15 seconds for the Django HTTP endpoint before giving up; misconfigured settings will surface there.
