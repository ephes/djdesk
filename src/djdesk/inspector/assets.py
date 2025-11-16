from __future__ import annotations

from pathlib import Path

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
    """Return True if an offline docs build exists on disk."""
    return docs_bundle_index().exists()


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
