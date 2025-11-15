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

- `uv run python manage.py runserver` – start the Django development server.
- `just install` – install/update dependencies via `uv sync`.
- `just test` – run Django's test suite (`manage.py test tests`) with `DJANGO_ENV=test`.
- `just lint` – run Ruff’s lint checks across the codebase; use `uv run ruff format .` to auto-format.
- `just hooks` – run every pre-commit hook against the full codebase.

### Settings and environments

Settings live in `djdesk/settings/` and are selected via `DJANGO_ENV`:

- `local` (default for `manage.py`): debug on, console email backend, localhost hosts.
- `test`: deterministic key, in-memory SQLite DB, fast password hasher, locmem email.
- `production`: forces secrets/hosts/DB config via env vars and turns on secure cookies.

Override the environment by exporting `DJANGO_ENV` (and optionally `DJANGO_SETTINGS_MODULE`) before running Django commands, for example:

```bash
export DJANGO_ENV=production
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
