# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DJDesk is a Django-based desktop application with an Electron wrapper. The Electron app uses a bundled python-build-standalone interpreter (Python 3.14) with Django and all dependencies pre-installed, falling back to system Python if the bundle is missing. The project uses a layered Django settings architecture (base/local/test/production).

## Project Structure

```
src/djdesk/              # Django application source
  settings/
    base.py              # Shared settings (imported by all environments)
    local.py             # Development settings (DEBUG=True)
    test.py              # Test settings (in-memory DB, fast hasher)
    production.py        # Production settings (expects env vars)
electron/                # Electron desktop wrapper
  build-django.js        # Downloads python-build-standalone, builds django-bundle
  main.js                # Electron entry point
  django-bundle/         # Self-contained Python + Django (git-ignored)
docs/                    # Sphinx documentation (builds to _build/html)
tests/                   # Django test suite
specs/                   # Internal planning notes (NEVER commit these!)
```

## Essential Commands

### Development
```bash
just install              # uv sync (install deps + editable package)
just dev                  # Start Django dev server (local settings)
just test                 # Run Django tests (test settings)
just lint                 # Run Ruff checks
just hooks                # Run all pre-commit hooks
```

### Electron Desktop App
```bash
just electron-install     # cd electron && npm install
just electron-bundle      # Build django-bundle with python-build-standalone
just electron-start       # Launch Electron app
just electron-build       # Package for current OS (macOS/Linux/Windows)
```

### Documentation
```bash
just docs-html            # Build static HTML
just docs-serve           # Live-reload server for editing
```

### Artifact Downloads (GitHub Actions)
```bash
just electron-download-latest        # All platforms
just electron-download-macos         # macOS only
just electron-download-windows       # Windows only
just electron-download-linux         # Linux only
just electron-download RUN_ID=<id>   # Specific workflow run
just electron-clean-artifacts        # Remove downloaded zips
```

## Settings Architecture

Three settings modules in `src/djdesk/settings/`:
- **base.py**: Shared foundation (imported via star import by all modules)
- **local.py**: Development defaults (no env vars required)
- **test.py**: Testing overrides (deterministic, fast)
- **production.py**: Expects `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, etc.

Default for `manage.py` and `just dev`: `djdesk.settings.local`
Default for `just test`: `djdesk.settings.test`
Override via `DJANGO_SETTINGS_MODULE` environment variable.

## Electron Bundle Build Process

`electron/build-django.js` performs:
1. Downloads python-build-standalone Python 3.14.0+20251031 for the current platform
2. Verifies SHA256 checksums
3. Unpacks into `electron/django-bundle/python/`
4. Installs dependencies from `pyproject.toml` (production deps only)
5. Copies `manage.py` and `src/djdesk/` into the bundle
6. Runs `collectstatic --clear` into `django-bundle/staticfiles`
7. Writes `run_django.py` launcher and `VERSION` file
8. Verifies Django can be imported

The bundle is platform-specific and ignored by Git.

## GitHub Actions Workflow

`.github/workflows/electron-desktop.yml` runs via manual dispatch only (use `just electron-workflow-run` to trigger). Builds macOS, Windows, and Linux in parallel using a matrix strategy. Each job:
1. Runs `npm ci` in electron/
2. Executes `npm run bundle` to create django-bundle
3. Calls `npm run build -- --<platform>`
4. Uploads `electron/dist` as workflow artifact

Artifacts can be downloaded via `just electron-download-*` commands.

## Tool Setup

- **uv**: Dependency management (replaces pip/venv workflows)
- **just**: Task runner (see justfile for all recipes)
- **pre-commit**: Git hooks for Ruff + hygiene checks
- **Ruff**: Linter and formatter (target: py311, line-length: 100)

## Important Constraints

1. **Never commit files under `specs/` directory** - These are internal planning notes that must remain untracked. Keep the `.gitignore` rule intact.
2. **Respect pre-commit hooks** - Ruff enforces Django-specific rules (DJ001-DJ999)
3. **Use uv and just recipes** - ensures consistency across contributors
4. **Settings isolation**: Star imports in settings modules are intentional (see pyproject.toml ruff ignores)
5. **Python 3.14+** required (see pyproject.toml requires-python)
6. **Always update documentation and changelog** - When making changes, update relevant documentation in `docs/` and add entries to `CHANGELOG.md`
7. **Run `just test` and `just lint` before committing** - Ensure all tests pass and code meets linting standards before creating commits

## Read the Docs

Full documentation lives at https://djdesk.readthedocs.io/en/latest/
- Config: `readthedocs.yml`
- Source: `docs/` directory (reST/Markdown)
- Branch `main` publishes automatically
