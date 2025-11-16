#!/usr/bin/env python3
"""Create or refresh the tutorial sample projects."""

from __future__ import annotations

import argparse
from pathlib import Path

from djdesk.inspector import samples


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Optional destination directory (defaults to settings.INSPECTOR_SAMPLE_ROOT).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing sample directories instead of skipping them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = samples.ensure_sample_projects(root=args.root, force=args.force)
    for slug, path, created in results:
        status = "created" if created else "skipped"
        print(f"[{status}] {slug} -> {path}")


if __name__ == "__main__":
    main()
