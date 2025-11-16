#!/usr/bin/env python3
"""Copy the Sphinx HTML build into the offline docs bundle directory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _prepare_destination(source: Path, dest: Path) -> None:
    temp_dest = dest.parent / f".{dest.name}.tmp"
    backup_dest = dest.parent / f".{dest.name}.old"

    if temp_dest.exists():
        shutil.rmtree(temp_dest)
    shutil.copytree(source, temp_dest)

    if backup_dest.exists():
        shutil.rmtree(backup_dest)

    if dest.exists():
        os.replace(dest, backup_dest)

    os.replace(temp_dest, dest)

    if backup_dest.exists():
        shutil.rmtree(backup_dest)


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
