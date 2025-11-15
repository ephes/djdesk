set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List all available recipes (default when running `just` with no args).
help:
    just --list

# Run the project's unit tests via `uv` and the unittest test discovery.
test:
    uv run python -m unittest discover -s tests -t .
