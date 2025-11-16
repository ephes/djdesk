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

# Build using the current host OS (macOS/Linux/Windows).
electron-build:
    case "$(uname | tr '[:upper:]' '[:lower:]')" in \
        darwin*) cd electron && npm run build -- --mac ;; \
        linux*) cd electron && npm run build -- --linux ;; \
        msys*|mingw*|cygwin*) cd electron && npm run build -- --win ;; \
        *) echo "Unsupported platform"; exit 1 ;; \
    esac
# List recent GitHub Action runs for electron builds (requires gh CLI).
electron-runs:
    gh run list --workflow=electron-desktop -L 10

# Download artifacts from the latest successful workflow run.
electron-download-latest:
    latest_run=$(gh run list --workflow=electron-desktop -L 1 --json databaseId,conclusion --jq 'map(select(.conclusion == "success")) | .[0].databaseId') && \
    if [ -z "$latest_run" ]; then \
        echo "No successful electron-desktop run found."; \
        exit 1; \
    fi && \
    gh run download "$latest_run" --dir dist-artifacts

# Download artifacts for a specific run (pass RUN_ID=<id>).
electron-download RUN_ID:
    gh run download "{{RUN_ID}}" --dir dist-artifacts

# Trigger the GitHub Actions electron-desktop workflow (manual run).
electron-workflow-run:
    gh workflow run electron-desktop.yml

# Remove downloaded GitHub artifact directories.
electron-clean-artifacts:
    rm -rf dist-artifacts
