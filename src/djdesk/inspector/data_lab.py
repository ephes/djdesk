from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
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
            "Explore tail logs exported from the inspector without mutating the "
            "project."
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
    path = _data_lab_root() / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    """Persist the rendered notebook to disk and return its path."""
    notebook = build_notebook(workspace, template_slug)
    workspace_dir = workspace_data_lab_dir(workspace)
    path = workspace_dir / f"{template_slug}.ipynb"
    path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")
    return path


def list_workspace_exports(workspace: Workspace) -> list[dict[str, Any]]:
    workspace_dir = workspace_data_lab_dir(workspace)
    exports: list[dict[str, Any]] = []
    for path in sorted(workspace_dir.glob("*.ipynb")):
        slug = path.stem
        template = DATA_LAB_TEMPLATE_MAP.get(slug)
        exports.append(
            {
                "slug": slug,
                "title": template["title"] if template else slug,
                "description": template["description"] if template else "",
                "path": str(path),
                "display_path": path.name,
                "modified_at": path.stat().st_mtime,
            }
        )
    return exports


def load_notebook(workspace: Workspace, slug: str) -> dict[str, Any]:
    path = workspace_data_lab_dir(workspace) / f"{slug}.ipynb"
    if not path.exists():
        msg = f"Notebook '{slug}' has not been exported for workspace '{workspace.slug}'."
        raise FileNotFoundError(msg)
    return json.loads(path.read_text(encoding="utf-8"))


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
                "<div class='nb-output'><header>Output</header>"
                f"{''.join(rendered_outputs)}</div>"
            )
    return (
        "<section class='nb-cell nb-cell--code'>"
        f"<pre><code>{code}</code></pre>"
        f"{outputs_html}"
        "</section>"
    )
