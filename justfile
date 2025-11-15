set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

test:
    uv run python -m unittest discover -s tests -t .
