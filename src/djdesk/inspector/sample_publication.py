"""Locked sample directory swaps and conservative restart recovery."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
import uuid
import warnings
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .filesystem import sync_descriptor

try:  # pragma: no cover - absent on Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


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


@contextmanager
def _directory(path, *, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def _write_files(directory: int, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Invalid sample file path")
        with ExitStack() as stack:
            parent = directory
            ancestors = [directory]
            for name in path.parts[:-1]:
                try:
                    os.mkdir(name, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = stack.enter_context(_directory(name, dir_fd=parent))
                ancestors.append(parent)
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=parent,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content.rstrip() + "\n")
                handle.flush()
                sync_descriptor(handle.fileno())
            for ancestor in reversed(ancestors):
                sync_descriptor(ancestor)
    sync_descriptor(directory)


def prepare_sample_root(root: Path) -> None:
    missing = []
    ancestor = root
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    root.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _sync_directory(directory.parent)


def _identity(path, *, dir_fd=None) -> list[int] | None:
    try:
        info = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"Sample path needs manual inspection (not a directory): {path}")
    return [info.st_dev, info.st_ino]


def _fd_identity(fd: int) -> list[int]:
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino]


def _check_root(root: Path, fd: int) -> None:
    if _identity(root) != _fd_identity(fd):
        raise OSError(f"Sample root name changed; preserve owned root identity {_fd_identity(fd)}")


def sample_directory_exists(destination: Path, *, root_fd: int | None) -> bool:
    """Admit an existing directory without following links under the sample lock."""
    if root_fd is None:  # pragma: no cover - Windows can only reuse existing samples
        return _identity(destination) is not None
    identity = _identity(destination.name, dir_fd=root_fd)
    _check_root(destination.parent, root_fd)
    return identity is not None


@contextmanager
def sample_root_owner(root: Path):
    """Keep one root generation across lock acquisition, recovery and publication."""
    if os.name == "nt":  # pragma: no cover - existing samples may still be skipped
        yield None
        return
    with _directory(root) as fd:
        _check_root(root, fd)
        yield fd
        _check_root(root, fd)


@contextmanager
def sample_lock(root: Path, root_fd: int, slug: str, *, timeout: float = 10):
    """Hold the stable per-sample lock created inside the held root generation."""
    _check_root(root, root_fd)
    name = f".{slug}.lock"
    # Never unlink this stable lock: waiters must share the same inode.
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(name, flags, 0o644, dir_fd=root_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"Sample lock needs manual inspection (not a regular file): {name}")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for sample lock {root / name}") from None
                time.sleep(0.05)
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for sample lock {root / name}")
            # The lock inode is anchored to root_fd; the root name must still point there.
            _check_root(root, root_fd)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _check_names(destination, root_fd, stage_fd, intent):
    if (
        _identity(destination.parent) != _fd_identity(root_fd)
        or _fd_identity(root_fd) != intent["root"]
        or _identity(intent["stage"]["name"], dir_fd=root_fd) != _fd_identity(stage_fd)
        or _fd_identity(stage_fd) != intent["stage"]["identity"]
    ):
        raise OSError("Sample root/staging name changed; preserve all related paths")


def _state(destination, root_fd, stage_fd):
    return (
        _identity(destination.name, dir_fd=root_fd),
        _identity("backup", dir_fd=stage_fd),
        _identity("replacement", dir_fd=stage_fd),
    )


def _check_state(destination, root_fd, stage_fd, intent, expected):
    _check_names(destination, root_fd, stage_fd, intent)
    if _state(destination, root_fd, stage_fd) != expected:
        raise OSError("Sample generation changed; preserve all related paths")


def _unlink_intent(destination, root_fd, identity):
    name = _intent_path(destination).name
    info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino] != identity:
        raise OSError("Sample intent changed; preserve all related paths")
    os.unlink(name, dir_fd=root_fd)
    sync_descriptor(root_fd)


def _intent_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.swap.json"


def _unique_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate swap intent member")
        result[key] = value
    return result


def _read_intent(destination: Path, root_fd: int):
    journal = _intent_path(destination)
    try:
        fd = os.open(journal.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
            raise ValueError("Unsafe swap intent file")
        content = handle.read(info.st_size + 1)
        after = os.fstat(handle.fileno())
        if (
            len(content) != info.st_size
            or after.st_size != info.st_size
            or after.st_mtime_ns != info.st_mtime_ns
            or after.st_ctime_ns != info.st_ctime_ns
            or after.st_nlink != 1
        ):
            raise ValueError("Incomplete or changed swap intent file")
        intent = json.loads(content, object_pairs_hook=_unique_members)
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
    return intent, [info.st_dev, info.st_ino]


def recover_sample(destination: Path, *, root_fd: int | None = None) -> bool:
    """Called only under the stable publisher lock; never delete interrupted trees."""
    journal = _intent_path(destination)
    intent = None
    if os.name == "nt":  # pragma: no cover
        if journal.exists() or journal.is_symlink():
            raise OSError("Sample recovery requires directory fsync (unsupported on Windows)")
        return False
    try:
        with ExitStack() as stack:
            if root_fd is None:
                root_fd = stack.enter_context(sample_root_owner(destination.parent))
            _check_root(destination.parent, root_fd)
            result = _read_intent(destination, root_fd)
            if result is None:
                _check_root(destination.parent, root_fd)
                return False
            intent, journal_identity = result
            stage = destination.parent / intent["stage"]["name"]
            stage_fd = stack.enter_context(_directory(stage.name, dir_fd=root_fd))
            current, backup, replacement = expected = _state(destination, root_fd, stage_fd)
            old, new = intent["old"], intent["new"]
            _check_state(destination, root_fd, stage_fd, intent, expected)
            if current == new and replacement is None and backup == old:
                pass  # Installed generation is visible; retain the backup.
            elif current is None and backup == old and replacement == new:
                source = "backup" if old is not None else "replacement"
                _check_state(destination, root_fd, stage_fd, intent, expected)
                os.rename(source, destination.name, src_dir_fd=stage_fd, dst_dir_fd=root_fd)
                expected = (old, None, new) if old is not None else (new, None, None)
            elif current == old and backup is None and replacement == new:
                pass  # The old name was never removed (or rollback finished).
            else:
                raise ValueError("Swap generation identities do not match")
            sync_descriptor(stage_fd)
            sync_descriptor(root_fd)
            _check_state(destination, root_fd, stage_fd, intent, expected)
            _unlink_intent(destination, root_fd, journal_identity)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        failure = OSError(
            f"Sample swap needs manual inspection: {journal}; preserve all related paths"
        )
        if intent is not None:
            failure.add_note(
                f"Retained root identity {intent['root']}, staging {intent['stage']}; "
                "recorded paths may have moved"
            )
        raise failure from error
    warnings.warn(
        f"Retained interrupted sample files for manual inspection: {stage}; "
        "recovered sample kept even with --force. Inspect before replacing it again.",
        RuntimeWarning,
    )
    return True


def _remove_tree(name, parent_fd, identity, check_names):
    # Open the admitted generation before deleting any contents. Never reopen its
    # absolute pathname, even if its parent/name is moved during retirement.
    with _directory(name, dir_fd=parent_fd) as fd:
        if _fd_identity(fd) != identity:
            raise OSError("Cleanup generation changed; preserve all related paths")

        def check():
            check_names()
            if _identity(name, dir_fd=parent_fd) != identity:
                raise OSError("Cleanup name changed; preserve all related paths")

        check()
        with os.scandir(fd) as entries:
            for entry in entries:
                check()
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.name, dir_fd=fd)
                else:
                    os.unlink(entry.name, dir_fd=fd)
        check()
        os.rmdir(name, dir_fd=parent_fd)


def _cleanup_stage(destination, root_fd, stage_fd, intent, expected):
    _check_state(destination, root_fd, stage_fd, intent, expected)
    installed, backup, replacement = expected

    def check_retirement():
        _check_names(destination, root_fd, stage_fd, intent)
        if backup is not None and _identity(destination.name, dir_fd=root_fd) != installed:
            raise OSError("Installed sample generation changed; preserve all related paths")

    for name, identity in (("backup", backup), ("replacement", replacement)):
        if identity is not None:
            _remove_tree(name, stage_fd, identity, check_retirement)
    check_retirement()
    os.rmdir(intent["stage"]["name"], dir_fd=root_fd)


def publish_sample(destination: Path, files: dict[str, str], *, root_fd: int | None = None) -> None:
    """Publish through held directory owners; caller owns the stable sample lock."""
    if os.name == "nt" or not shutil.rmtree.avoids_symlink_attacks:  # pragma: no cover
        raise OSError("Sample publication requires safe directory-relative filesystem APIs")
    # Establish the root's directory entry before promising durable children.
    _sync_directory(destination.parent.parent)
    with ExitStack() as stack:
        if root_fd is None:
            root_fd = stack.enter_context(sample_root_owner(destination.parent))
        _check_root(destination.parent, root_fd)
        sync_descriptor(root_fd)
        old_identity = _identity(destination.name, dir_fd=root_fd)
        stage_name = f".{destination.name}-{uuid.uuid4().hex}"
        os.mkdir(stage_name, 0o700, dir_fd=root_fd)
        stage = destination.parent / stage_name
        stage_identity = _identity(stage_name, dir_fd=root_fd)
        stage_fd = stack.enter_context(_directory(stage_name, dir_fd=root_fd))
        intent = {
            "version": 1,
            "root": _fd_identity(root_fd),
            "stage": {"name": stage_name, "identity": stage_identity},
            "old": old_identity,
            "new": None,
        }
        journal = _intent_path(destination)
        promoted = False
        journal_identity = None
        expected = (intent["old"], None, None)
        try:
            _check_state(destination, root_fd, stage_fd, intent, expected)
            os.mkdir("replacement", dir_fd=stage_fd)
            with _directory("replacement", dir_fd=stage_fd) as replacement_fd:
                intent["new"] = _fd_identity(replacement_fd)
                expected = (intent["old"], None, intent["new"])
                _write_files(replacement_fd, files)
            sync_descriptor(stage_fd)
            _check_state(destination, root_fd, stage_fd, intent, expected)
            fd = os.open(
                journal.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                journal_identity = _fd_identity(handle.fileno())
                json.dump(intent, handle)
                handle.flush()
                sync_descriptor(handle.fileno())
            sync_descriptor(root_fd)
            try:
                _check_state(destination, root_fd, stage_fd, intent, expected)
                if intent["old"] is not None:
                    os.rename(destination.name, "backup", src_dir_fd=root_fd, dst_dir_fd=stage_fd)
                    expected = (None, intent["old"], intent["new"])
                    sync_descriptor(stage_fd)
                    sync_descriptor(root_fd)
                _check_state(destination, root_fd, stage_fd, intent, expected)
                os.rename("replacement", destination.name, src_dir_fd=stage_fd, dst_dir_fd=root_fd)
            except BaseException as error:
                try:
                    # A failed rename may have changed names before raising. Admit
                    # only the exact before/after-backup states for restoration.
                    _check_names(destination, root_fd, stage_fd, intent)
                    state = _state(destination, root_fd, stage_fd)
                    if intent["old"] is not None and state == (None, intent["old"], intent["new"]):
                        _check_state(destination, root_fd, stage_fd, intent, state)
                        os.rename(
                            "backup", destination.name, src_dir_fd=stage_fd, dst_dir_fd=root_fd
                        )
                        sync_descriptor(stage_fd)
                        sync_descriptor(root_fd)
                    expected = (intent["old"], None, intent["new"])
                    _check_state(destination, root_fd, stage_fd, intent, expected)
                    _unlink_intent(destination, root_fd, journal_identity)
                    journal_identity = None
                except BaseException as restore_error:
                    error.add_note(
                        f"Restoration refused/failed: {restore_error}; "
                        f"recovery copy: {stage / 'backup'}"
                    )
                raise
            promoted = True
            expected = (intent["new"], intent["old"], None)
            try:
                sync_descriptor(stage_fd)
                sync_descriptor(root_fd)
                _check_state(destination, root_fd, stage_fd, intent, expected)
                _unlink_intent(destination, root_fd, journal_identity)
            except OSError as error:
                raise SampleDurabilityError(destination, stage) from error
            journal_identity = None
            _cleanup_stage(destination, root_fd, stage_fd, intent, expected)
        except BaseException as error:
            location = (
                f"{stage} (root identity {intent['root']}, "
                f"staging identity {intent['stage']['identity']}; paths may have moved)"
            )
            if promoted:
                error.add_note(f"New sample installed at {destination}; retained files: {location}")
            elif journal_identity is not None:
                error.add_note(f"Recovery intent: {journal}; retained sample files: {location}")
            else:
                try:
                    _cleanup_stage(destination, root_fd, stage_fd, intent, expected)
                except OSError as cleanup_error:
                    error.add_note(f"Retained sample staging {location}: {cleanup_error}")
            raise
