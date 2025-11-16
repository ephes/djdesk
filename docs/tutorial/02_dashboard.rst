Stage 2 — Dashboard & Project Explorer
======================================

Stage 2 is where the DJDesk Project Inspector feels alive. The seeded workspace fills
every pane so contributors can study the layout even before wiring their own project.

Layout overview
---------------

* **Toolbar** — lives in ``inspector/templates/inspector/dashboard.html``. The "Docs"
  button toggles the assistant drawer so documentation is always one click away.
* **Left rail** — lists :class:`~djdesk.inspector.models.Workspace` objects and
  the scan queue. Drag a folder onto the dropzone (Electron exposes ``File.path``)
  to pre-populate the wizard with the selected path.
* **Canvas tabs** — ``Schema``, ``Migrations``, ``Code``, and ``Results`` map to the
  ``tab-panel`` elements. ``app.js`` handles tab state and periodically pulls fresh
  data from ``/api/workspaces/<slug>/status/``.
* **Log stream** — displays ``workspace.metadata["log_excerpt"]`` and is refreshed
  alongside the scan queue.

Data sources
------------

The dashboard is intentionally server-rendered: ``DashboardView`` builds the initial
context and :func:`djdesk.inspector.services.workspace_status_payload` keeps everything
fresh via polling. That makes the tutorial easy to follow in the docs because you can
open DevTools and inspect the exact payloads shown in the walkthrough.

What to capture for the docs
----------------------------

* Annotated screenshot showing the toolbar, dual rails, center canvas, and assistant.
* JSON snippet of the ``/api/workspaces/<slug>/status/`` response so readers can see
  how ``insights``, ``schema``, and ``log_excerpt`` map to UI elements.
* Notes on theming — the CSS lives in ``inspector/static/inspector/app.css`` and uses
  simple gradients so contributors can tweak colors without touching a build pipeline.
