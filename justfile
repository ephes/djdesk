set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List all available recipes (default when running `just` with no args).
help:
    just --list

# Install project dependencies (and the package itself) via uv.
install:
    uv sync

# Run Ruff lint checks against the whole project.
lint:
    uv run ruff check .

# Run the Django development server.
dev:
    DJANGO_SETTINGS_MODULE=djdesk.settings.local uv run python manage.py runserver

# Run the project's pre-commit hooks against all files.
hooks:
    uv run pre-commit run --all-files

# Run Django's test suite via manage.py with the test settings.
test:
    DJANGO_SETTINGS_MODULE=djdesk.settings.test uv run python manage.py test tests

# Install Electron dependencies under ./electron (once per clone or after package upgrades).
electron-install:
    cd electron && npm install

# Build the bundled Django payload used by the Electron shell.
electron-bundle:
    cd electron && npm run bundle

# Launch the Electron shell (prefers the bundled Python when available).
electron-start:
    cd electron && npm start

# Build production assets for each OS (must run on matching host OS).
electron-build-macos:
    cd electron && npm run build -- --mac

electron-build-windows:
    cd electron && npm run build -- --win

electron-build-linux:
    cd electron && npm run build -- --linux
