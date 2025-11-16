## Documentation

Full tutorial + reference lives on Read the Docs: https://djdesk.readthedocs.io/en/latest/

```bash
uv pip install -r docs/requirements.txt  # once per machine
just docs-html                           # build static HTML into docs/_build/html
just docs-serve                          # autoreload while writing
```

Read the Docs config lives in `readthedocs.yml`; `main` publishes to the URL above.

## Development

### Prerequisites

- Python 3.14 or newer (see `pyproject.toml`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task automation (optional but recommended)

### Initial setup

```bash
# Install runtime + dev dependencies into .venv using the lockfile.
# uv installs the djdesk package itself in editable mode, so imports work without PYTHONPATH tweaks.
just install

# Install git hooks so Ruff + hygiene checks run before each commit
uv run pre-commit install
```

The `just install` recipe wraps `uv sync` so dependency installs stay consistent across contributors.

> `uv sync` installs dev dependencies by default. Use `uv sync --no-dev` if you intentionally want a
> leaner environment. Activating `.venv/bin/activate` is optional—`uv run …` automatically executes
> commands inside the managed environment.
>
> Using a different workflow? Run `pip install --editable .` (or `uv pip install -e .`) inside your
> virtualenv so `djdesk` stays importable without modifying `sys.path`.

### Everyday commands

- `just dev` – start the Django development server (`manage.py runserver`) using `djdesk.settings.local`.
- `just install` – install/update dependencies via `uv sync`.
- `just test` – run Django's test suite (`manage.py test tests`) using `djdesk.settings.test`.
- `just lint` – run Ruff’s lint checks across the codebase; use `uv run ruff format .` to auto-format.
- `just hooks` – run every pre-commit hook against the full codebase.
- `just docs-html` – build the Sphinx documentation (`docs/_build/html`).
- `just docs-serve` – live-reload the docs while editing reST/Markdown files.
- `just electron-install` / `just electron-start` – install Electron deps and launch the local shell.
- `just electron-bundle` / `just electron-build-*` – create the python-build-standalone bundle and packaged binaries for each OS.
- `just electron-runs` – list recent GitHub Actions builds; `just electron-workflow-run` triggers the matrix build.
- `just electron-download-latest` / `just electron-download-macos` / `just electron-download-windows` / `just electron-download-linux` / `just electron-download RUN_ID=<id>` – download workflow artifacts (all platforms or per platform); `just electron-clean-artifacts` removes the downloaded zips.

### Settings and environments

Settings live in `djdesk/settings/`; choose the appropriate module via `DJANGO_SETTINGS_MODULE`:

- `djdesk.settings.local` (default for `manage.py` and `just dev`): debug on, console email backend, localhost hosts.
- `djdesk.settings.test` (used by `just test`): deterministic key, in-memory SQLite DB, fast password hasher, locmem email.
- `djdesk.settings.production` (default for ASGI/WSGI entry points): expects secrets/hosts/DB config from env vars and enables secure cookies.

Override the module explicitly whenever you need something different, e.g.:

```bash
export DJANGO_SETTINGS_MODULE=djdesk.settings.production
export DJANGO_SECRET_KEY="super-secret"
export DJANGO_ALLOWED_HOSTS="example.com"
uv run python manage.py migrate
```

### Electron desktop shell (Phase 1)

You can exercise the minimal Electron wrapper that spawns the Django dev server with your local interpreter:

```bash
# 1. Ensure backend deps are installed (from repo root)
uv pip install -e .

# 2. Install the Electron dependencies
cd electron
npm install

# 3. Launch the shell (optionally pin a Python binary)
PYTHON=$(command -v python3.14 || command -v python3) npm start
```

`npm start` starts Django on an open port, waits for it to respond, and then shows the site in an Electron window. Close the window (or press `Ctrl+C` in the terminal) to shut down both Electron and Django.
