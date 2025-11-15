## Development

### Prerequisites

- Python 3.14 or newer (see `pyproject.toml`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task automation (optional but recommended)

### Initial setup

```bash
# Install runtime + dev dependencies into .venv using the lockfile
uv sync --extra dev

# Activate the environment (or rely on `uv run …` for ad-hoc execution)
source .venv/bin/activate

# Install git hooks so Ruff + hygiene checks run before each commit
uv run pre-commit install
```

### Everyday commands

- `uv run python manage.py runserver` – start the Django development server.
- `just test` – run the unit test suite via `uv run python -m unittest …`.
- `uv run ruff check .` / `uv run ruff format .` – lint or auto-format using Ruff.
- `uv run pre-commit run --all-files` – dry-run all hooks locally.

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
