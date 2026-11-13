from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

from django.conf import settings


def _repo_root() -> Path:
    """Return the root of the git repository (matches settings.BASE_DIR)."""
    base = getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[3])
    return Path(base).resolve()


def sample_root() -> Path:
    """
    Directory where the tutorial sample projects are unpacked.

    Defaults to ``BASE_DIR / "sample_projects"`` but can be overridden via the
    ``DJDESK_SAMPLE_ROOT`` environment variable.
    """
    raw = getattr(
        settings,
        "INSPECTOR_SAMPLE_ROOT",
        _repo_root() / "sample_projects",
    )
    return Path(raw).expanduser().resolve()


def sample_project_path(slug: str) -> str:
    """Return an absolute path to the sample workspace referenced by ``slug``."""
    return str(sample_root() / slug)


def docs_bundle_root() -> Path:
    """
    Directory containing the offline Sphinx bundle used by the Docs drawer.
    """
    raw = getattr(
        settings,
        "INSPECTOR_DOCS_BUNDLE_ROOT",
        _repo_root() / "var" / "docs_bundle",
    )
    return Path(raw).expanduser().resolve()


def docs_bundle_index() -> Path:
    """Path to the offline docs entry point (``index.html``)."""
    return docs_bundle_root() / "index.html"


def docs_bundle_available() -> bool:
    """Return True if an offline docs build can be read safely on this platform."""
    return hasattr(os, "O_NONBLOCK") and docs_bundle_index().exists()


def resolve_docs_asset(path_fragment: str | None) -> Path:
    """
    Map a relative docs resource path to an on-disk file inside the bundle.

    Raises ValueError if the resolved path escapes the docs bundle directory.
    """
    root = docs_bundle_root()
    safe_fragment = path_fragment or ""
    target = root / safe_fragment
    candidate = target if target.name else target / "index.html"
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Offline docs asset '{safe_fragment}' not found.") from exc
    root_resolved = root.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise ValueError("Attempted to read outside of the docs bundle directory.")
    if resolved.is_dir():
        resolved = resolved / "index.html"
        if not resolved.exists():
            raise FileNotFoundError(f"Offline docs asset '{safe_fragment}' missing index.html.")
    return resolved


def open_docs_asset(path_fragment: str | None) -> tuple[Path, BinaryIO]:
    """Return an owned regular-file stream from the admitted bundle generation."""
    root = docs_bundle_root()
    admitted_root = root.stat()
    path = resolve_docs_asset(path_fragment)
    parts = path.relative_to(root).parts
    if not parts or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("Safe offline asset reading is unavailable.")
    with ExitStack() as stack:
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, flags)
            stack.callback(os.close, directory)
            if not os.path.samestat(admitted_root, os.fstat(directory)):
                raise ValueError("Offline bundle changed while opening.")
            for part in parts[:-1]:
                directory = os.open(part, flags, dir_fd=directory)
                stack.callback(os.close, directory)
            before = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Offline asset must be a regular file.")
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, fd)
            if not os.path.samestat(before, os.fstat(fd)):
                raise ValueError("Offline asset changed while opening.")
        else:
            # Check the whole path around the nonblocking open on portable readers.
            parents = [root, *[root.joinpath(*parts[:i]) for i in range(1, len(parts))]]
            identities = [parent.lstat() for parent in parents]
            before = path.lstat()
            if not all(stat.S_ISDIR(info.st_mode) for info in identities) or not stat.S_ISREG(
                before.st_mode
            ):
                raise ValueError("Offline assets must be regular files in real directories.")
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            if (
                not os.path.samestat(admitted_root, identities[0])
                or not os.path.samestat(before, os.fstat(fd))
                or not os.path.samestat(before, path.lstat())
                or any(
                    not os.path.samestat(info, parent.lstat())
                    for parent, info in zip(parents, identities)
                )
            ):
                raise ValueError("Offline asset changed while opening.")
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Offline asset must be a regular file.")
        # Duplicate only the leaf; ExitStack closes all admission descriptors.
        owned_fd = os.dup(fd)
        try:
            return path, os.fdopen(owned_fd, "rb")
        except BaseException:
            os.close(owned_fd)
            raise
