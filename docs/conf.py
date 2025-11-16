"""Sphinx configuration for DJDesk documentation."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import django

# -- Path setup --------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djdesk.settings.local")
django.setup()

# -- Project information -----------------------------------------------------

project = "DJDesk"
copyright = f"{datetime.now():%Y}, DJDesk contributors"
author = "DJDesk team"
release = "0.1.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.todo",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns: list[str] = ["_build", "Thumbs.db", ".DS_Store"]

nitpicky = False

autodoc_typehints = "description"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

todo_include_todos = True

myst_heading_anchors = 3
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

# -- HTML output -------------------------------------------------------------

html_theme = "furo"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["mermaid-init.js"]

html_title = "DJDesk Documentation"
html_theme_options = {
    "light_logo": "",
    "dark_logo": "",
    "source_repository": "https://github.com/ephes/djdesk",
    "source_branch": "main",
    "source_directory": "docs/",
}

# -- Mermaid configuration ---------------------------------------------------

mermaid_version = "11.2.0"
