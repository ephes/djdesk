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

# Run the project's pre-commit hooks against all files.
hooks:
    uv run pre-commit run --all-files

# Run the project's unit tests via `uv` and the unittest test discovery.
test:
    uv run python -m unittest discover -s tests -t .
