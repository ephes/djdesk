#!/usr/bin/env python3
"""Copy the Sphinx HTML build into the offline docs bundle directory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from djdesk.docs_bundle import REQUIRED_HTML_FILES


def _contains_directory(tree: Path, path: Path) -> bool:
    return any(ancestor.exists() and tree.samefile(ancestor) for ancestor in (path, *path.parents))


def _missing_html_member(root: Path) -> str | None:
    for relative in REQUIRED_HTML_FILES:
        asset = root / relative
        if not asset.is_file() or asset.stat().st_size == 0:
            return relative
    return None


def _directory_identity(path: Path) -> list[int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Expected a real directory at {path}")
    return [info.st_dev, info.st_ino]


def _recover_destination(dest: Path, receipt: Path) -> None:
    if not os.path.lexists(receipt):
        return
    try:
        if not stat.S_ISREG(receipt.lstat().st_mode):
            raise ValueError("Recovery receipt must be a regular file")
        work_name, old, new = json.loads(receipt.read_text(encoding="utf-8"))
        if (
            not isinstance(work_name, str)
            or Path(work_name).name != work_name
            or not work_name.casefold().startswith(f".{dest.name}-publish-".casefold())
            or not all(
                isinstance(identity, list)
                and len(identity) == 2
                and all(type(part) is int for part in identity)
                for identity in (old, new)
            )
            or old == new
        ):
            raise ValueError("Invalid recovery receipt")
        work = dest.parent / work_name
        if _directory_identity(work) is None:
            raise ValueError(f"Missing publication workspace {work}")
        backup = work / "backup"
        live = _directory_identity(dest)
        saved = _directory_identity(backup)
        if live is None and saved == old:
            if missing := _missing_html_member(backup):
                raise ValueError(f"Recovery copy {backup} is missing a nonempty {missing}")
            os.replace(backup, dest)
        elif (live, saved) not in ((old, None), (new, old)):
            raise ValueError(f"Unexpected bundle or backup in {work}")
        receipt.unlink()
    except (OSError, ValueError, TypeError) as error:
        error.add_note(f"Inspect {receipt} and its publication workspace with publishers stopped.")
        raise
    print(
        f"Recovered offline docs at {dest}. Retained publication workspace {work}",
        file=sys.stderr,
    )


def _prepare_destination(source: Path, dest: Path) -> None:
    dest = dest.resolve()
    lock_path = dest.parent / f".{dest.name}.lock"
    receipt = dest.parent / f".{dest.name}.recovery.json"
    if os.path.lexists(receipt):
        with FileLock(lock_path, timeout=10):
            _recover_destination(dest, receipt)
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise NotADirectoryError(f"Docs build is not a directory: {source}")
    if _contains_directory(source, dest) or (dest.exists() and _contains_directory(dest, source)):
        raise ValueError(f"Docs source and destination must be separate trees: {source}, {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{dest.name}-publish-", dir=dest.parent))
    staging = work / "staging"
    backup = work / "backup"
    promoted = False
    recovery_pending = False
    try:
        shutil.copytree(source, staging)
        if missing := _missing_html_member(staging):
            raise ValueError(
                f"Incomplete Sphinx build at {source}: {missing} must be a nonempty file. "
                "Run `just docs-html` and retry with the rebuilt HTML directory."
            )
        manifest = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": str(source),
        }
        (staging / "bundle.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Leave this lock file in place: unlinking it can split waiting writers.
        with FileLock(lock_path, timeout=10):
            _recover_destination(dest, receipt)
            old = _directory_identity(dest)
            if old is not None:
                intent = work / "recovery.json"
                intent.write_text(
                    json.dumps([work.name, old, _directory_identity(staging)]), encoding="utf-8"
                )
                try:
                    os.rename(intent, receipt)
                    recovery_pending = True
                except BaseException:
                    recovery_pending = os.path.lexists(receipt)
                    raise
            try:
                if old is not None:
                    os.replace(dest, backup)
                os.replace(staging, dest)
            except BaseException as error:
                if recovery_pending:
                    try:
                        if _directory_identity(dest) is None and _directory_identity(backup) == old:
                            os.replace(backup, dest)
                        if _directory_identity(dest) == old and not os.path.lexists(backup):
                            receipt.unlink()
                            recovery_pending = False
                    except BaseException as restore_error:
                        error.add_note(f"Restoration failed: {restore_error}")
                raise
            promoted = True
            if recovery_pending:
                receipt.unlink()
                recovery_pending = False
            shutil.rmtree(work)
    except BaseException as error:
        if promoted:
            error.add_note(
                f"New bundle installed at {dest}; leftover workspace {work} may be incomplete"
            )
        elif recovery_pending:
            error.add_note(f"Recovery copy retained at {backup}. See {receipt}")
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

    try:
        _prepare_destination(source, dest)
    except FileNotFoundError:
        if not source.exists():
            raise SystemExit(
                f"Docs build not found at {source}. Run `just docs-html` first."
            ) from None
        raise

    print(f"Offline docs copied to {dest}")


if __name__ == "__main__":
    main()
