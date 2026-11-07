#!/usr/bin/env python3
"""Copy the Sphinx HTML build into the offline docs bundle directory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock


def _prepare_destination(source: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{dest.name}-publish-", dir=dest.parent))
    staging = work / "staging"
    backup = work / "backup"
    promoted = False
    try:
        shutil.copytree(source, staging)
        # Leave this lock file in place: unlinking it can split waiting writers.
        with FileLock(dest.parent / f".{dest.name}.lock", timeout=10):
            if dest.exists():
                os.replace(dest, backup)
            try:
                os.replace(staging, dest)
            except BaseException as error:
                if backup.exists():
                    try:
                        os.replace(backup, dest)
                    except BaseException as restore_error:
                        error.add_note(f"Restoration failed: {restore_error}")
                raise
            promoted = True
            shutil.rmtree(work)
    except BaseException as error:
        if promoted:
            error.add_note(
                f"New bundle installed at {dest}; leftover workspace {work} may be incomplete"
            )
        elif backup.exists():
            error.add_note(f"Recovery copy retained at {backup}")
        else:
            try:
                shutil.rmtree(work)
            except OSError as cleanup_error:
                error.add_note(f"Could not clean staging directory {work}: {cleanup_error}")
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to the built HTML docs (defaults to docs/_build/html).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Destination directory (defaults to var/docs_bundle).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    source = args.source or (repo_root / "docs" / "_build" / "html")
    dest = args.dest or (repo_root / "var" / "docs_bundle")

    if not source.exists():
        raise SystemExit(f"Docs build not found at {source}. Run `just docs-html` first.")

    _prepare_destination(source, dest)

    manifest = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(source),
    }
    (dest / "bundle.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Offline docs copied to {dest}")


if __name__ == "__main__":
    main()
