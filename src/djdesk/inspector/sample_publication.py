"""Locked sample directory swaps and conservative restart recovery."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import warnings
from pathlib import Path

from .filesystem import sync_descriptor


class SampleDurabilityError(OSError):
    """The visible generation must not be rolled back or automatically retried."""

    def __init__(self, destination: Path, stage: Path):
        super().__init__(
            f"Sample already installed at {destination}, but durability is uncertain; "
            f"do not retry automatically. Inspect retained files at {stage}"
        )


def _sync_directory(path: Path) -> None:
    if os.name == "nt":  # pragma: no cover - Windows cannot fsync directory descriptors
        raise OSError(
            "Durable sample publication requires directory fsync (unsupported on Windows)"
        )
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        sync_descriptor(fd)
    finally:
        os.close(fd)


def _sync_tree(path: Path) -> None:
    for directory, _, files in os.walk(path, topdown=False):
        for name in files:
            fd = os.open(Path(directory) / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                sync_descriptor(fd)
            finally:
                os.close(fd)
        _sync_directory(Path(directory))


def prepare_sample_root(root: Path) -> None:
    missing = []
    ancestor = root
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    root.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _sync_directory(directory.parent)


def _identity(path: Path) -> list[int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"Sample path needs manual inspection (not a directory): {path}")
    return [info.st_dev, info.st_ino]


def _intent_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.swap.json"


def _unique_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate swap intent member")
        result[key] = value
    return result


def _read_intent(destination: Path):
    journal = _intent_path(destination)
    if os.name == "nt":  # pragma: no cover
        if journal.exists() or journal.is_symlink():
            raise OSError("Sample recovery requires directory fsync (unsupported on Windows)")
        return None
    try:
        fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
            raise ValueError("Unsafe swap intent file")
        intent = json.loads(handle.read(16385), object_pairs_hook=_unique_members)
    if not isinstance(intent, dict) or set(intent) != {"version", "root", "stage", "old", "new"}:
        raise ValueError("Invalid swap intent fields")
    if type(intent["version"]) is not int or intent["version"] != 1:
        raise ValueError("Unknown swap intent version")
    stage = intent["stage"]
    if (
        not isinstance(stage, dict)
        or set(stage) != {"name", "identity"}
        or not isinstance(stage["name"], str)
        or Path(stage["name"]).name != stage["name"]
        or not stage["name"].startswith(f".{destination.name}-")
    ):
        raise ValueError("Invalid swap staging path")
    identities = [intent["root"], stage["identity"], intent["new"]]
    if intent["old"] is not None:
        identities.append(intent["old"])
    for identity in identities:
        if not (
            isinstance(identity, list)
            and len(identity) == 2
            and all(type(value) is int and value >= 0 for value in identity)
        ):
            raise ValueError("Invalid swap directory identity")
    return intent


def recover_sample(destination: Path) -> bool:
    """Called only under the stable publisher lock; never delete interrupted trees."""
    journal = _intent_path(destination)
    try:
        intent = _read_intent(destination)
        if intent is None:
            return False
        stage = destination.parent / intent["stage"]["name"]
        if (
            _identity(destination.parent) != intent["root"]
            or _identity(stage) != intent["stage"]["identity"]
        ):
            raise ValueError("Swap root/staging identity changed")
        current = _identity(destination)
        backup = _identity(stage / "backup")
        replacement = _identity(stage / "replacement")
        old, new = intent["old"], intent["new"]
        if current == new and replacement is None and backup == old:
            pass  # New sample is already visible; retain the old backup for inspection.
        elif current is None and backup == old and replacement == new:
            source = stage / ("backup" if old is not None else "replacement")
            source.rename(destination)
        elif current == old and backup is None and replacement == new:
            pass  # The old name was never removed (or rollback finished).
        else:
            raise ValueError("Swap generation identities do not match")
        _sync_directory(stage)
        _sync_directory(destination.parent)
        journal.unlink()
        _sync_directory(destination.parent)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise OSError(
            f"Sample swap needs manual inspection: {journal}; preserve all related paths"
        ) from error
    warnings.warn(
        f"Retained interrupted sample files for manual inspection: {stage}; "
        "recovered sample kept even with --force. Inspect before replacing it again.",
        RuntimeWarning,
    )
    return True


def publish_sample(destination: Path, files: dict[str, str]) -> None:
    """Publish a complete tree; caller owns the stable destination lock."""
    # Establish the root's own directory entry before promising durable children.
    _sync_directory(destination.parent.parent)
    _sync_directory(destination.parent)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    replacement, backup = stage / "replacement", stage / "backup"
    journal = _intent_path(destination)
    promoted = False
    journal_owned = False
    try:
        replacement.mkdir()
        for relative_path, content in files.items():
            full_path = replacement / relative_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        _sync_tree(replacement)
        _sync_directory(stage)
        intent = {
            "version": 1,
            "root": _identity(destination.parent),
            "stage": {"name": stage.name, "identity": _identity(stage)},
            "old": _identity(destination),
            "new": _identity(replacement),
        }
        fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            journal_owned = True
            json.dump(intent, handle)
            handle.flush()
            sync_descriptor(handle.fileno())
        _sync_directory(destination.parent)
        try:
            if intent["old"] is not None:
                destination.rename(backup)
                _sync_directory(stage)
                _sync_directory(destination.parent)
            replacement.rename(destination)
        except BaseException as error:
            if backup.exists():
                try:
                    if destination.exists() or destination.is_symlink():
                        raise OSError("Destination changed; refusing to overwrite during rollback")
                    backup.rename(destination)
                    _sync_directory(stage)
                    _sync_directory(destination.parent)
                except BaseException as restore_error:
                    error.add_note(f"Restoration failed: {restore_error}; recovery copy: {backup}")
                    raise error
            journal.unlink()
            _sync_directory(destination.parent)
            journal_owned = False
            raise
        promoted = True
        try:
            _sync_directory(stage)
            _sync_directory(destination.parent)
            journal.unlink()
            _sync_directory(destination.parent)
        except OSError as error:
            raise SampleDurabilityError(destination, stage) from error
        journal_owned = False
        shutil.rmtree(stage)
    except BaseException as error:
        if promoted:
            error.add_note(
                f"New sample installed at {destination}; inspect retained files at {stage}"
            )
        elif backup.exists() or journal_owned:
            error.add_note(f"Recovery intent: {journal}; retained sample files: {stage}")
        else:
            try:
                shutil.rmtree(stage)
            except OSError as cleanup_error:
                error.add_note(f"Could not clean sample staging {stage}: {cleanup_error}")
        raise
