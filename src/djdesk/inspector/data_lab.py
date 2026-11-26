from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import ExitStack, contextmanager, suppress
from datetime import datetime
from datetime import timezone as datetime_timezone
from itertools import islice
from pathlib import Path
from typing import Any

try:  # pragma: no cover - fcntl is absent on Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from django.conf import settings
from django.urls.converters import SlugConverter
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .filesystem import sync_descriptor
from .models import Workspace

logger = logging.getLogger(__name__)

DATA_LAB_TEMPLATES = [
    {
        "slug": "schema-audit",
        "title": "Schema audit starter",
        "description": "Summarise discovered models and pending migrations.",
        "tags": ["schema", "inspector"],
        "cells": [
            {
                "id": "schema-overview",
                "type": "markdown",
                "source": [
                    "# Workspace schema audit — {{WORKSPACE_NAME}}\n",
                    (
                        "This seeded notebook mirrors the inspector's schema canvas and "
                        "surfaces the same SAFE commands you can dispatch from DJDesk.\n"
                    ),
                ],
            },
            {
                "id": "read-schema-snapshot",
                "type": "code",
                "source": [
                    "import json\n",
                    "from pathlib import Path\n",
                    "\n",
                    "schema = json.loads(Path('schema_snapshot.json').read_text())\n",
                    "print(f\"Nodes: {len(schema.get('nodes', []))}\")\n",
                    "for node in schema.get('nodes', []):\n",
                    "    print(f\"- {node['name']} ({len(node.get('fields', []))} fields)\")\n",
                ],
            },
            {
                "id": "safe-command-reference",
                "type": "markdown",
                "source": [
                    "## SAFE command reference\n",
                    (
                        "Use the Task Runner drawer to dispatch read-only commands. "
                        "Tokens and execution policies inherit DJDesk's SAFE command "
                        "contract.\n"
                    ),
                ],
            },
        ],
    },
    {
        "slug": "log-study",
        "title": "Log study scratchpad",
        "description": (
            "Explore tail logs exported from the inspector without mutating the project."
        ),
        "tags": ["logs"],
        "cells": [
            {
                "id": "log-overview",
                "type": "markdown",
                "source": [
                    "# Log excerpts — {{WORKSPACE_NAME}}\n",
                    (
                        "The inspector captured a snapshot from {{WORKSPACE_PATH}}. "
                        "Use this tab to review notable events offline.\n"
                    ),
                ],
            },
            {
                "id": "read-log-excerpt",
                "type": "code",
                "source": [
                    "from pathlib import Path\n",
                    "\n",
                    "log_path = Path('log_excerpt.txt')\n",
                    "print('Log snapshot available:', log_path.exists())\n",
                    "print(log_path.read_text()[:400])\n",
                ],
            },
        ],
    },
]

DATA_LAB_TEMPLATE_MAP = {template["slug"]: template for template in DATA_LAB_TEMPLATES}
EXPORT_SCAN_LIMIT = 512
EXPORT_RESULT_LIMIT = 50
EXPORT_CACHE_SIZE = 32
EXPORT_SCAN_ATTEMPTS = 3
NOTEBOOK_READ_ATTEMPTS = 3
NOTEBOOK_FILE_BYTES = 8 * 1024 * 1024
NOTEBOOK_VIEW_CELLS = 500
NOTEBOOK_VIEW_OUTPUTS_PER_CELL = 50
NOTEBOOK_VIEW_TEXT_BYTES = 1024 * 1024
NOTEBOOK_VIEW_HTML_BYTES = 2 * 1024 * 1024
# Directory timestamps can be coarse (jiffies on Linux, 1-2 s on HFS+/FAT/SMB), so a
# change in the same tick as a scan keeps the generation unchanged. Like Git's racy-clean
# rule, a window is cached only once the directory timestamps are clearly in the past.
EXPORT_RACY_WINDOW_NS = 3_000_000_000


def _replacement_table(workspace: Workspace) -> dict[str, str]:
    return {
        "WORKSPACE_NAME": workspace.name,
        "WORKSPACE_SLUG": workspace.slug,
        "WORKSPACE_PATH": workspace.project_path,
    }


def _apply_placeholders(value: str, replacements: dict[str, str]) -> str:
    rendered = value
    for key, text in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", text)
    return rendered


def workspace_data_lab_dir(workspace: Workspace) -> Path:
    """Return the directory where notebooks for ``workspace`` live."""
    slug = workspace.slug or f"workspace-{workspace.pk}"
    if not re.fullmatch(SlugConverter.regex, slug):
        raise ValueError("Invalid workspace notebook directory name.")
    root = Path(settings.INSPECTOR_DATA_LAB_ROOT).resolve()
    path = root / slug
    if path.parent != root or path.is_symlink():
        raise ValueError("Workspace notebook directory must be directly inside the export root.")
    return path


_export_root_lock = threading.Lock()
# New directory entries awaiting a successful sync, grouped by their parent.
_unsynced_root_entries: dict[Path, set[Path]] = {}


def _provision_export_root(root: Path) -> None:
    """Create a missing export root and sync the entries of every directory created.

    A later export retries pending entries on its own root's directory chain.
    """
    with _export_root_lock:
        candidate = root
        while not os.path.lexists(candidate) and candidate.parent != candidate:
            _unsynced_root_entries.setdefault(candidate.parent, set()).add(candidate)
            candidate = candidate.parent
        root.mkdir(parents=True, exist_ok=True)
        for entry in (root, *root.parents):
            parent = entry.parent
            if entry not in _unsynced_root_entries.get(parent, ()):
                continue
            try:
                _sync_directory(parent)
            except FileNotFoundError:
                # A removed parent no longer holds the entry; a missing root is
                # detected again by the next export.
                pass
            # A successful parent sync covers all its entries, including siblings.
            del _unsynced_root_entries[parent]


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _sync(fd)
    finally:
        _close_descriptor(fd, path)


def _close_descriptor(fd: int, path: Path, *, preserve_outcome: bool = True) -> None:
    try:
        os.close(fd)
    except OSError:
        if not preserve_outcome:
            raise
        with suppress(Exception):
            logger.warning("Could not close notebook export descriptor for %s", path, exc_info=True)


@contextmanager
def _open_workspace_directory(workspace_dir: Path, *, create: bool = False):
    """Keep directory-relative operations on the admitted workspace inode."""
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(workspace_dir.parent, flags)
        stack.callback(_close_descriptor, root_fd, workspace_dir.parent, preserve_outcome=create)
        if create:
            try:
                os.mkdir(workspace_dir.name, dir_fd=root_fd)
            except FileExistsError:
                pass
        directory_fd = os.open(workspace_dir.name, flags, dir_fd=root_fd)
        stack.callback(_close_descriptor, directory_fd, workspace_dir, preserve_outcome=create)
        yield root_fd, directory_fd


@contextmanager
def _open_notebook(workspace: Workspace, filename: str):
    """Open a regular export, refusing symlinks, including directory symlinks."""
    try:
        workspace_dir = workspace_data_lab_dir(workspace)
        if Path(filename).name != filename:
            raise ValueError("Invalid notebook filename.")
        with ExitStack() as stack:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                _, workspace_fd = stack.enter_context(_open_workspace_directory(workspace_dir))
                fd = os.open(filename, flags | os.O_NOFOLLOW, dir_fd=workspace_fd)
                stack.callback(os.close, fd)
            else:
                path = workspace_dir / filename
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Notebook must be a regular file.")
                fd = os.open(path, flags)
                stack.callback(os.close, fd)
                if (
                    workspace_dir.is_symlink()
                    or not os.path.samestat(before, os.fstat(fd))
                    or not os.path.samestat(before, path.lstat())
                ):
                    raise ValueError("Notebook changed while opening.")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("Notebook must be a regular file.")
                yield handle
    except (UnicodeError, InvalidNotebookError):
        raise
    except (OSError, ValueError) as exc:
        raise FileNotFoundError("Notebook export is unavailable.") from exc


def template_summary() -> list[dict[str, Any]]:
    """Return lightweight metadata for UI dropdowns."""
    return [
        {
            "slug": template["slug"],
            "title": template["title"],
            "description": template["description"],
            "tags": template.get("tags", []),
        }
        for template in DATA_LAB_TEMPLATES
    ]


def build_notebook(workspace: Workspace, template_slug: str) -> dict[str, Any]:
    template = DATA_LAB_TEMPLATE_MAP.get(template_slug)
    if template is None:
        msg = f"Unknown Data Lab template '{template_slug}'."
        raise ValueError(msg)

    replacements = _replacement_table(workspace)
    cells = []
    for raw in template["cells"]:
        source = raw.get("source", [])
        if isinstance(source, str):
            source = [source]
        source_lines = [_apply_placeholders(line, replacements) for line in source]
        cell = {
            "id": raw["id"],
            "cell_type": raw["type"],
            "metadata": raw.get("metadata", {}),
            "source": source_lines,
        }
        if raw["type"] == "code":
            cell["execution_count"] = None
            cell["outputs"] = raw.get("outputs", [])
        cells.append(cell)

    return {
        "cells": cells,
        "metadata": {"djdesk_template": template_slug},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _sync(fd: int) -> None:
    sync_descriptor(fd, fcntl_module=fcntl)


class NotebookDurabilityError(OSError):
    """A complete export is visible, but its directory sync failed."""

    def __init__(self, published_path: Path):
        self.published_path = published_path
        super().__init__(
            f"The notebook '{published_path.name}' is already published, but its durability "
            "could not be confirmed. Inspect the export directory before exporting again."
        )


def _retire_notebook_stage(staging: Path, directory_fd: int, owned: os.stat_result) -> None:
    try:
        current = os.stat(staging.name, dir_fd=directory_fd, follow_symlinks=False)
        if os.path.samestat(owned, current):
            os.unlink(staging.name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    except OSError:
        with suppress(Exception):
            logger.warning("Could not remove notebook staging file %s", staging, exc_info=True)


def export_notebook(workspace: Workspace, template_slug: str) -> Path:
    """Publish a complete notebook without replacing any existing filename."""
    notebook = build_notebook(workspace, template_slug)
    if not (
        {os.open, os.mkdir, os.link, os.unlink, os.stat} <= os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and os.link in os.supports_follow_symlinks
    ):
        raise ValueError("Safe notebook export is unavailable on this platform.")
    content = json.dumps(notebook, indent=2)
    workspace_dir = workspace_data_lab_dir(workspace)
    _provision_export_root(workspace_dir.parent)
    with (
        _open_workspace_directory(workspace_dir, create=True) as (root_fd, directory_fd),
        ExitStack() as stage,
    ):
        staging = f".notebook-{uuid.uuid4().hex}.tmp"
        fd = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        staging_path = workspace_dir / staging
        stage.callback(_close_descriptor, fd, staging_path)
        owned = os.fstat(fd)
        stage.callback(_retire_notebook_stage, staging_path, directory_fd, owned)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            _sync(fd)
        suffix = 1
        while True:
            filename = template_slug if suffix == 1 else f"{template_slug}-{suffix}"
            filename += ".ipynb"
            if not os.path.samestat(
                owned, os.stat(staging, dir_fd=directory_fd, follow_symlinks=False)
            ):
                raise ValueError("Notebook staging file changed before publication.")
            try:
                os.link(
                    staging,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                suffix += 1
                continue
            published_path = workspace_dir / filename
            try:
                _sync(directory_fd)
                # Cover an entry created by another exporter whose root sync is pending.
                _sync(root_fd)
            except OSError as exc:
                raise NotebookDurabilityError(published_path) from exc
            # Both directory syncs completed. Retirement is best effort.
            return published_path


def export_template(slug: str) -> dict[str, Any]:
    """Resolve template metadata consistently from an export's filename stem."""
    if slug not in DATA_LAB_TEMPLATE_MAP:
        base, _, suffix = slug.rpartition("-")
        if suffix.isdigit():
            slug = base
    return DATA_LAB_TEMPLATE_MAP.get(slug, {})


def _directory_generation(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Notebook inventory requires a real directory.")
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


class _GenerationChanged(ValueError):
    pass


_export_name_cache: OrderedDict[
    tuple[str, tuple[int, int, int, int]], tuple[tuple[str, ...], bool]
] = OrderedDict()
_export_name_cache_lock = threading.Lock()


def _scan_export_names(
    directory: Path, generation: tuple[int, int, int, int]
) -> tuple[tuple[str, ...], bool]:
    # Bound enumeration itself, including hidden/unrelated entries, before sorting.
    with os.scandir(directory) as entries:
        window = list(islice(entries, EXPORT_SCAN_LIMIT + 1))
    names = sorted(
        entry.name
        for entry in window[:EXPORT_SCAN_LIMIT]
        if entry.name.endswith(".ipynb")
        and re.fullmatch(SlugConverter.regex, Path(entry.name).stem)
        and entry.is_file(follow_symlinks=False)
    )
    if _directory_generation(directory) != generation:
        raise _GenerationChanged("Notebook directory changed during inventory.")
    incomplete = len(window) > EXPORT_SCAN_LIMIT or len(names) > EXPORT_RESULT_LIMIT
    return tuple(names[:EXPORT_RESULT_LIMIT]), incomplete


def _export_names(directory: Path) -> tuple[tuple[str, ...], bool]:
    """Return the bounded filename window, cached per settled directory generation."""
    for _attempt in range(EXPORT_SCAN_ATTEMPTS):
        generation = _directory_generation(directory)
        key = (str(directory), generation)
        with _export_name_cache_lock:
            cached = _export_name_cache.get(key)
            if cached is not None:
                _export_name_cache.move_to_end(key)
                return cached
        try:
            result = _scan_export_names(directory, generation)
        except _GenerationChanged:
            # A concurrent export or cleanup; rescan rather than report an empty window.
            continue
        settled = time.time_ns() - max(generation[2], generation[3]) > EXPORT_RACY_WINDOW_NS
        if settled:
            with _export_name_cache_lock:
                _export_name_cache[key] = result
                _export_name_cache.move_to_end(key)
                while len(_export_name_cache) > EXPORT_CACHE_SIZE:
                    _export_name_cache.popitem(last=False)
        return result
    raise ValueError("Notebook directory kept changing during inventory.")


def workspace_export_inventory(workspace: Workspace) -> tuple[list[dict[str, Any]], bool]:
    """Return a bounded, generation-cached filename window and completeness flag."""
    try:
        workspace_dir = workspace_data_lab_dir(workspace)
        names, incomplete = _export_names(workspace_dir)
    except FileNotFoundError:
        return [], False
    except (OSError, ValueError):
        return [], True
    exports: list[dict[str, Any]] = []
    for name in names:
        path = workspace_dir / name
        try:
            with _open_notebook(workspace, path.name) as handle:
                modified_at = os.fstat(handle.fileno()).st_mtime
            # Admit the UTC display domain before a bad timestamp reaches any consumer.
            datetime.fromtimestamp(modified_at, tz=datetime_timezone.utc)
        except (OSError, ValueError, OverflowError):
            incomplete = True
            continue
        slug = path.stem
        template = export_template(slug)
        exports.append(
            {
                "slug": slug,
                "title": template["title"] if template else slug,
                "description": template["description"] if template else "",
                "path": str(path),
                "display_path": path.name,
                "modified_at": modified_at,
            }
        )
    return exports, incomplete


def list_workspace_exports(workspace: Workspace) -> list[dict[str, Any]]:
    """Compatibility list API; use workspace_export_inventory for completeness."""
    return workspace_export_inventory(workspace)[0]


class InvalidNotebookError(ValueError):
    """The edited export cannot be rendered by the notebook viewer."""


class NotebookSizeError(InvalidNotebookError):
    """The complete view exceeds its supported work envelope."""


def _unique_notebook_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidNotebookError("Notebook JSON members must be unique.")
        result[key] = value
    return result


def load_notebook(workspace: Workspace, slug: str) -> dict[str, Any]:
    try:
        for _attempt in range(NOTEBOOK_READ_ATTEMPTS):
            with _open_notebook(workspace, f"{slug}.ipynb") as handle:
                before = os.fstat(handle.fileno())
                if before.st_size > NOTEBOOK_FILE_BYTES:
                    raise NotebookSizeError("Notebook exceeds the supported viewer size.")
                # Bound this read even if an editor keeps appending to the inode.
                content = handle.read(before.st_size + 1)
                after = os.fstat(handle.fileno())
            if (
                len(content) == before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
                and before.st_ctime_ns == after.st_ctime_ns
            ):
                break
        else:
            raise FileNotFoundError(
                "Notebook kept changing while reading; finish editing and reload."
            )
        notebook = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_notebook_members)
    except InvalidNotebookError:
        raise
    except (ValueError, RecursionError) as exc:
        raise InvalidNotebookError("Notebook is not valid UTF-8 JSON.") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells", []), list):
        raise InvalidNotebookError("Notebook cells must be a list.")
    _admit_notebook_view(notebook)
    return notebook


def _admit_notebook_view(notebook: dict[str, Any]) -> None:
    """Check the complete view before allocating any HTML fragments."""
    if len(notebook.get("cells", [])) > NOTEBOOK_VIEW_CELLS:
        raise NotebookSizeError("Too many notebook cells.")
    text_bytes = 0
    html_bytes = 0

    def charge_text(value: Any, invalid_text: str, *, markdown: bool = False) -> None:
        nonlocal text_bytes, html_bytes
        lines = [value] if isinstance(value, str) else value
        if not isinstance(lines, list):
            raise InvalidNotebookError(invalid_text)
        for line in lines:
            if not isinstance(line, str):
                raise InvalidNotebookError(invalid_text)
            try:
                size = len(line.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise InvalidNotebookError(invalid_text) from exc
            text_bytes += size
            # Exact escaped byte count, without constructing escaped text.
            html_bytes += size + sum(
                line.count(char) * extra
                for char, extra in (("&", 4), ("<", 3), (">", 3), ('"', 5), ("'", 5))
            )
        if markdown:
            raw = "".join(lines)
            normalized = _normalize_markdown_newlines(raw)
            # Charge original text bytes, but count CRLF as one byte in HTML.
            html_bytes -= len(raw) - len(normalized)
            # Conservatively include stripped/empty paragraphs and every newline.
            html_bytes += len("<section class='nb-cell nb-cell--markdown'></section>")
            html_bytes += (normalized.count("\n") + 1) * len("<p></p>")
        # A malformed line takes precedence over an oversized field. Empty lists
        # leave wrapper accounting to the end of the cell.
        if lines or markdown:
            check_budget()

    def check_budget() -> None:
        if text_bytes > NOTEBOOK_VIEW_TEXT_BYTES or html_bytes > NOTEBOOK_VIEW_HTML_BYTES:
            raise NotebookSizeError("Notebook text or HTML exceeds the supported viewer size.")

    invalid_source = "Notebook cell source must be text or a list of text."
    invalid_output = "Notebook outputs must contain text objects."
    for cell in notebook.get("cells", []):
        if not isinstance(cell, dict):
            raise InvalidNotebookError(invalid_source)
        cell_type = cell.get("cell_type")
        charge_text(cell.get("source", []), invalid_source, markdown=cell_type == "markdown")
        if cell_type == "code":
            html_bytes += len(
                "<section class='nb-cell nb-cell--code'><pre><code></code></pre></section>"
            )
            outputs = cell.get("outputs", [])
            if not isinstance(outputs, list):
                raise InvalidNotebookError(invalid_output)
            if len(outputs) > NOTEBOOK_VIEW_OUTPUTS_PER_CELL:
                raise NotebookSizeError("Too many notebook outputs in one cell.")
            if outputs:
                html_bytes += len("<div class='nb-output'><header>Output</header></div>")
            for output in outputs:
                if not isinstance(output, dict):
                    raise InvalidNotebookError(invalid_output)
                charge_text(output.get("text", []), invalid_output)
                html_bytes += len("<pre></pre>")
        check_budget()


def render_notebook_html(notebook: dict[str, Any]) -> str:
    """Convert a subset of notebook JSON into styled HTML blocks."""
    parts: list[str] = []
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") == "markdown":
            parts.append(_render_markdown_cell(cell))
        elif cell.get("cell_type") == "code":
            parts.append(_render_code_cell(cell))
    return mark_safe("".join(parts))


def _normalize_markdown_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _render_markdown_cell(cell: dict[str, Any]) -> str:
    text = _normalize_markdown_newlines("".join(cell.get("source", [])))
    paragraphs = [para.strip() for para in text.split("\n\n") if para.strip()]
    content = "".join(f"<p>{escape(para)}</p>" for para in paragraphs)
    return f"<section class='nb-cell nb-cell--markdown'>{content}</section>"


def _render_code_cell(cell: dict[str, Any]) -> str:
    code = escape("".join(cell.get("source", [])))
    outputs_html = ""
    outputs = cell.get("outputs", [])
    if outputs:
        rendered_outputs = []
        for output in outputs:
            text = "".join(output.get("text", []))
            if text.strip():
                rendered_outputs.append(f"<pre>{escape(text)}</pre>")
        if rendered_outputs:
            outputs_html = (
                f"<div class='nb-output'><header>Output</header>{''.join(rendered_outputs)}</div>"
            )
    return (
        "<section class='nb-cell nb-cell--code'>"
        f"<pre><code>{code}</code></pre>"
        f"{outputs_html}"
        "</section>"
    )
