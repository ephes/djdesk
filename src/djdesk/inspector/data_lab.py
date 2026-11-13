from __future__ import annotations

import json
import os
import re
import stat
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from django.conf import settings
from django.urls.converters import SlugConverter
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Workspace

DATA_LAB_TEMPLATES = [
    {
        "slug": "schema-audit",
        "title": "Schema audit starter",
        "description": "Summarise discovered models and pending migrations.",
        "tags": ["schema", "inspector"],
        "cells": [
            {
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


def _data_lab_root() -> Path:
    root = Path(settings.INSPECTOR_DATA_LAB_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_data_lab_dir(workspace: Workspace) -> Path:
    """Return the directory where notebooks for ``workspace`` live."""
    slug = workspace.slug or f"workspace-{workspace.pk}"
    if not re.fullmatch(SlugConverter.regex, slug):
        raise ValueError("Invalid workspace notebook directory name.")
    root = _data_lab_root().resolve()
    path = root / slug
    if path.parent != root or path.is_symlink():
        raise ValueError("Workspace notebook directory must be directly inside the export root.")
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _open_workspace_directory(workspace: Workspace):
    """Keep directory-relative operations on the admitted workspace inode."""
    workspace_dir = workspace_data_lab_dir(workspace)
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(workspace_dir.parent, flags)
        stack.callback(os.close, root_fd)
        directory_fd = os.open(workspace_dir.name, flags, dir_fd=root_fd)
        stack.callback(os.close, directory_fd)
        yield workspace_dir, directory_fd


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
                _, workspace_fd = stack.enter_context(_open_workspace_directory(workspace))
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
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("Notebook must be a regular file.")
                yield handle
    except UnicodeError:
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


def export_notebook(workspace: Workspace, template_slug: str) -> Path:
    """Publish a complete notebook without replacing any existing filename."""
    notebook = build_notebook(workspace, template_slug)
    if not (
        {os.open, os.link, os.unlink, os.stat} <= os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and os.link in os.supports_follow_symlinks
    ):
        raise ValueError("Safe notebook export is unavailable on this platform.")
    content = json.dumps(notebook, indent=2)
    with _open_workspace_directory(workspace) as (workspace_dir, directory_fd):
        staging = f".notebook-{uuid.uuid4().hex}.tmp"
        fd = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        owned = os.fstat(fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(fd)
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
                return workspace_dir / filename
        finally:
            try:
                try:
                    current = os.stat(staging, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if os.path.samestat(owned, current):
                        os.unlink(staging, dir_fd=directory_fd)
            finally:
                os.close(fd)


def export_template(slug: str) -> dict[str, Any]:
    """Resolve template metadata consistently from an export's filename stem."""
    if slug not in DATA_LAB_TEMPLATE_MAP:
        base, _, suffix = slug.rpartition("-")
        if suffix.isdigit():
            slug = base
    return DATA_LAB_TEMPLATE_MAP.get(slug, {})


def list_workspace_exports(workspace: Workspace) -> list[dict[str, Any]]:
    try:
        workspace_dir = workspace_data_lab_dir(workspace)
    except ValueError:
        return []
    exports: list[dict[str, Any]] = []
    for path in sorted(workspace_dir.glob("*.ipynb")):
        if not re.fullmatch(SlugConverter.regex, path.stem):
            continue
        try:
            with _open_notebook(workspace, path.name) as handle:
                modified_at = os.fstat(handle.fileno()).st_mtime
        except FileNotFoundError:
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
    return exports


class InvalidNotebookError(ValueError):
    """The edited export cannot be rendered by the notebook viewer."""


def _is_notebook_text(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(line, str) for line in value)
    )


def load_notebook(workspace: Workspace, slug: str) -> dict[str, Any]:
    try:
        with _open_notebook(workspace, f"{slug}.ipynb") as handle:
            content = handle.read()
        notebook = json.loads(content)
    except (ValueError, RecursionError) as exc:
        raise InvalidNotebookError("Notebook is not valid UTF-8 JSON.") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells", []), list):
        raise InvalidNotebookError("Notebook cells must be a list.")
    for cell in notebook.get("cells", []):
        if not isinstance(cell, dict) or not _is_notebook_text(cell.get("source", [])):
            raise InvalidNotebookError("Notebook cell source must be text or a list of text.")
        if cell.get("cell_type") == "code":
            outputs = cell.get("outputs", [])
            if not isinstance(outputs, list) or any(
                not isinstance(output, dict) or not _is_notebook_text(output.get("text", []))
                for output in outputs
            ):
                raise InvalidNotebookError("Notebook outputs must contain text objects.")
    return notebook


def render_notebook_html(notebook: dict[str, Any]) -> str:
    """Convert a subset of notebook JSON into styled HTML blocks."""
    parts: list[str] = []
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") == "markdown":
            parts.append(_render_markdown_cell(cell))
        elif cell.get("cell_type") == "code":
            parts.append(_render_code_cell(cell))
    return mark_safe("".join(parts))


def _render_markdown_cell(cell: dict[str, Any]) -> str:
    text = "".join(cell.get("source", []))
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
