Integrating Django with Electron
================================

DJDesk doubles as a reference implementation for embedding any Django project inside Electron. This guide walks through the integration patterns end to end so you can copy the pieces that matter for your own app.

Integration path
----------------

- Boot Django from Electron (foundation for everything else)
- Render your existing Django UI and APIs inside the Electron renderer
- Layer optional desktop-only affordances (drag/drop, notifications, offline cues)
- Wire safe automation hooks for vetted command execution
- (Optional) Ship data exports or a “lab” experience alongside the inspector
- Package and distribute the bundled Django payload with platform installers

Use the sections below as standalone references—none depend on “stages” or prerequisite tutorials.

.. _guide-boot-django:

Boot Django from Electron
-------------------------

Objective
~~~~~~~~~

Prove the plumbing: Electron launches Django, serves it on ``http://127.0.0.1:<port>/``, and reuses the local settings module.

Prerequisites
~~~~~~~~~~~~~

* Python 3.14+ with ``uv`` on your ``PATH`` (manages dependencies and installs `djdesk` in editable mode)
* Node.js >=22.12.0 (Electron wrapper)
* ``just`` (optional helper, but recipes assume it)

Bootstrap commands
~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    just install            # uv sync --locked --extra dev + editable djdesk install
    just electron-install   # npm install inside ./electron
    just electron-start     # boots Django + Electron together

``dev`` is an optional extra containing Ruff and pre-commit, not a default dependency group.
For a runtime-only environment, use ``uv sync --locked`` without extras instead.

``electron/main.js`` detects the bundled Python interpreter (``electron/django-bundle/python``) and falls back to ``python3``/``python`` on PATH. It selects an open port via :mod:`get-port`, applies ``DJANGO_SETTINGS_MODULE=djdesk.settings.local`` when unset, and waits until ``/`` responds before loading the renderer window.

Smoke test checklist
~~~~~~~~~~~~~~~~~~~~

* Electron window shows the Convexity-inspired chrome plus the seeded “Atlas Telemetry” workspace.
* DevTools network tab shows polling against ``/api/workspaces/<slug>/status/``.
* Terminal logs display ``manage.py runserver`` bound to an ephemeral port chosen by Electron.

Troubleshooting tips
~~~~~~~~~~~~~~~~~~~~

* **Port conflicts** – set ``DJDESK_FORCE_PORT=8787`` before running ``npm start`` to pin the server.
* **Firewall prompts** – allow ``python3`` network access the first time macOS prompts so Electron can proxy.
* **Blank window** – press ``Cmd+Alt+I`` to open DevTools, check failed requests, then reload with ``Cmd+R`` without stopping Django.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Copy ``electron/main.js`` and ``electron/run_django.py`` patterns so Electron locates your interpreter and settings module.
2. Keep Django's ``runserver`` loop independent of the renderer—Electron should treat it like a local web server.
3. Add environment flags (``DJDESK_FORCE_PORT`` style) early so future packaging work stays predictable.

.. _guide-render-django:

Render Django UI in Electron
----------------------------

Objective
~~~~~~~~~

Render Django templates and APIs inside Electron, seed tutorial data, and prove that the renderer can submit forms + consume REST payloads.

Key pieces
~~~~~~~~~~

* **Server-rendered dashboard** – ``DashboardView`` injects ``workspace``, ``doc_links``, and ``task_presets`` context so the initial load mirrors production layout.
* **Workspace wizard** – ``WorkspaceWizardForm`` validates project metadata, generates slugs, and (optionally) seeds scan jobs. In Electron, dropping a folder auto-fills ``project_path``.
* **Status endpoint** – ``GET /api/workspaces/<slug>/status/`` returns scans, schema, insights, task history, and doc links. ``inspector/static/inspector/app.js`` polls it every few seconds.

Sample response (trimmed):

.. code-block:: json

    {
      "workspace": "atlas-telemetry-studio",
      "insights": [{"title": "Pending migrations", "value": "3"}],
      "apps": [{"label": "catalog.datasets", "pending_migrations": 1}],
      "schema": {"nodes": [{"name": "Dataset", "fields": ["id", "slug"]}]},
      "log_excerpt": [{"timestamp": "15:22:01", "level": "info", "message": "Running check..."}],
      "scans": [{"id": 1, "kind": "schema", "status": "completed", "progress": 100}],
      "tasks": [{"id": 5, "label": "Diff migrations", "status": "running"}],
      "doc_links": [{"title": "Dashboard overview", "url": "https://djdesk.readthedocs.io/en/latest/guide/"}]
    }

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Render your existing Django views inside Electron without rewriting them for a SPA—server templates keep the integration simple.
2. Seed predictable data (fixtures or migrations) so screenshots and QA runs stay deterministic.
3. Expose one consolidated status endpoint per workspace/project so the renderer can refresh UI sections with a single request.

Bootstrap reproducible tutorial fixtures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Screenshot parity depends on everyone importing the same sample projects. DJDesk ships the fixtures as generated bundles instead of checking them into git:

.. code-block:: bash

    just fetch-samples                 # creates sample_projects/
    tree sample_projects/djdesk        # inspect the Atlas Telemetry workspace

Behind the scenes ``scripts/fetch_samples.py`` writes the ``django-polls``, ``djdesk`` (Atlas Telemetry), and ``generated`` projects into ``INSPECTOR_SAMPLE_ROOT`` (defaults to ``sample_projects/``). ``content.py`` references these repo-relative paths so seeds, migrations, and screenshots always point at real files. Override ``DJDESK_SAMPLE_ROOT`` if you prefer to stash the bundles elsewhere.

Use ``uv run python scripts/fetch_samples.py --force`` to replace existing samples, including
all local edits, tutorial state and databases. Replacements are staged beside the destination
before publication. Write failures leave the previous sample untouched; publication failures
restore it. If rollback itself fails, recover the previous tree from
``.sample-slug-*/backup`` under the sample root.
Cleanup failures after publication report an error with the new sample already installed;
any leftover backup may be incomplete.

.. _guide-desktop-affordances:

Add desktop-only affordances (optional)
---------------------------------------

Why this matters
~~~~~~~~~~~~~~~~

Electron unlocks workflows that browsers restrict. DJDesk layers three native touches to showcase the difference:

* **Drag & drop wizard priming** – ``.workspace-dropzone`` captures ``webUtils.getPathForFile`` when running inside Electron. The preload script fires a ``djdesk:workspace-drop`` custom event, obtains the native path with ``webUtils.getPathForFile``, and stores it under ``djdesk.wizard.projectPath`` so the wizard can auto-fill ``project_path``. Browsers fall back to showing the filename and asking users to paste the path manually.
* **System notifications** – ``app.js`` watches polling responses and calls ``window.djdeskNative.notify`` when tasks finish. Electron routes the request through ``Notification`` in the main process; browsers fall back to the standard Web Notifications API.
* **Doc drawer deep links** – ``data-doc-link`` anchors now also call ``window.djdeskNative.openExternal`` so every click mirrors the same Read the Docs page in the user's default browser while the in-app drawer stays in sync. The main process only dispatches ``http(s)`` URLs requested by the top-level DJDesk page; see the *Shell boundary* section of :doc:`../architecture`.
* **Offline indicator** – ``data-offline-indicator`` listens to ``navigator.onLine`` to display “Offline-ready” vs “Synced”. Because docs, SQLite, and static assets ship with the build, the inspector keeps working without a network connection.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Use the preload script to expose OS integrations (paths, clipboard, drag/drop) in a controlled way.
2. Favor Web APIs Electron already supports (Notifications, clipboard) before building custom IPC.
3. Add graceful degradation paths so the same templates make sense when opened in a browser.

Bundle docs for offline parity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The inspector's Docs drawer now prefers a local Sphinx export so tutorial captures work offline:

.. code-block:: bash

    just docs-bundle                   # builds docs/_build/html + copies into var/docs_bundle
    ls var/docs_bundle/index.html      # entry point served by OfflineDocsView

``DashboardView`` checks ``var/docs_bundle/index.html`` and swaps the drawer + deep links to ``/docs/offline/...`` when available. The renderer still opens the external Read the Docs URL in the user's browser, but the iframe stays pinned to the offline build so QA and Electron demos render deterministically. Delete ``var/docs_bundle`` (or skip ``just docs-bundle``) to fall back to ``INSPECTOR_DOCS_BASE_URL``.

.. _guide-safe-automation:

Wire safe automation hooks
--------------------------

Objective
~~~~~~~~~

Demonstrate how Electron can launch vetted Django commands without turning into a general-purpose shell. DJDesk uses ``django-tasks`` to orchestrate pre-approved runs from the assistant drawer.

Flow summary
~~~~~~~~~~~~

1. ``TaskPreset`` rows provide command templates, labels, keys, docs URLs and icons.
2. The assistant drawer posts to ``POST /api/task-runs/`` with ``workspace``, ``preset``, optional ``notes``, and required ``confirm_safe`` confirmation. The form persists the approved command and workspace identity/path on a new ``WorkspaceTaskRun``.
3. ``execute_workspace_task`` (decorated with ``@django_tasks.task``) atomically claims the persisted run and executes the real management command in the approved workspace directory. It validates the snapshot against the current ``INSPECTOR_SAFE_COMMANDS`` and per-command argument policy before starting a subprocess, streams output into the run log, and records its outcome. This is real execution against the selected project; use a disposable synthetic workspace for demos. Legacy runs without approval snapshots are refused.
4. ``app.js`` merges the response into local UI state, fires desktop notifications when runs finish, and refreshes the status payload without waiting for the next poll.

Nonzero exits, timeouts, output limits and validation failures produce a failed run. Unexpected
execution errors are recorded and propagate to the task backend. An enqueue error cancels only
an unclaimed run; a running or completed outcome survives. Check task history before submitting
again after an uncertain dispatch. See :doc:`../concepts` for the command allowlist, read-only
argument rules, process cleanup and output limits, and :doc:`../api/rest_endpoints` for payloads.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Model allowed commands explicitly—never execute arbitrary shell input from the renderer.
2. Require the user to acknowledge read-only/guardrail policies (checkbox) before dispatching tasks.
3. Start with the synchronous ``ImmediateBackend`` (as in DJDesk) and swap in Celery/Redis later without changing the UI contract.

.. _guide-data-lab:

Ship rich exports or data labs (optional)
-----------------------------------------

Concept
~~~~~~~

DJDesk treats the Data Lab as an export-first experience:

1. The dashboard exposes a **Data Lab** panel without a stage feature flag.
2. Users pick a notebook template (schema audit, log study, etc.).
3. ``POST /api/workspaces/<slug>/data-lab/export/`` synchronously writes a template-based ``.ipynb`` into ``INSPECTOR_DATA_LAB_ROOT/<slug>`` and returns the updated listing. Existing files are preserved with numeric suffixes; ``data_lab.selected_notebook`` identifies the fresh export.
4. Selecting an export opens a static HTML preview rendered by ``inspector/data_lab.py``. No code cells are executed and no Jupyter kernel or server is launched.

``DJDESK_DATA_LAB_ROOT`` overrides the export root. By default it is ``var/data_lab`` under
the development project root, or under the packaged app's per-user state root (see below).
``DJDESK_FLAG_DATA_LAB_LIVE`` sets ``INSPECTOR_DATA_LAB_LIVE`` for the payload/viewer indicator
only; live execution is unfinished and enabling this flag does not start a kernel. There is
no ``--with-data-lab`` installer option.

Listings and previews accept regular notebook files directly inside the workspace export
directory. Notebook symlinks, even internal ones, and symlinked workspace export directories
are refused. On systems supporting directory-relative opens and no-follow flags, validation
is bound to the opened descriptors. The portable fallback checks file identity around opening.
Malformed UTF-8, JSON, cells or text outputs produce an HTTP 422 repair page with no notebook
content or parser traceback. Repair the export in your editor and reload. The preview does
not rewrite or execute it, and rendered source and output text remain HTML-escaped.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Decide whether rich exports are static (HTML snapshots) or interactive (kernels, sandboxes).
2. Store user exports outside replaceable application files; use a repo-ignored directory only for development fixtures.
3. Add separate execution infrastructure and policy if your application needs live kernels; DJDesk's shipped viewer is static.

.. _guide-package:

Package and distribute
----------------------

Objective
~~~~~~~~~

Bundle the Django payload with Electron so contributors can install/run DJDesk without managing Python. This section summarizes the workflow and points to :doc:`../electron` for exhaustive reference.

Packaged state and legacy recovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The packaged shell passes Electron's ``app.getPath('userData')`` as ``DJDESK_STATE_ROOT``.
Django stores ``db.sqlite3`` and, by default, ``var/data_lab/`` under that root, outside the
replaceable bundle. ``DJDESK_DATA_LAB_ROOT`` still overrides exports. Source development
keeps its project-local defaults unless ``DJDESK_STATE_ROOT`` is explicitly set. Offline
docs and static assets remain in the application payload.

Before rebuilding or replacing an older bundle, close DJDesk and back up its legacy
``electron/django-bundle/db.sqlite3`` and ``electron/django-bundle/var/data_lab/``
(installed copies use the same paths under the app's ``django-bundle`` resource). The builder
removes the entire old bundle. To recover, copy the closed database and exports into the
corresponding paths under the new per-user root before launch. Preserve any SQLite ``-wal``
and ``-shm`` companions with the database. If a destination already exists, back it up and
resolve the two datasets manually; do not overwrite it. DJDesk does not silently migrate or
merge legacy state. The new launcher applies migrations to the chosen database on startup.

Checklist
~~~~~~~~~

1. Run ``just electron-bundle`` (``npm run bundle``) to:
   - Download python-build-standalone into ``electron/django-bundle/python/``.
   - Install ``pyproject.toml`` dependencies inside the bundle.
   - Copy ``manage.py`` and ``src/djdesk`` into ``django-bundle/src/``.
   - Collect static files and write ``run_django.py`` + ``VERSION`` metadata.
2. Build installers per platform via ``just electron-build-*`` (macOS, Windows, Linux). Electron Builder packages the bundle via ``extraResources``.
3. Use GitHub Actions (`electron-desktop.yml`) or `just electron-workflow-run` to produce distributable artifacts.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Keep the bundler deterministic—delete previous payloads before rebuilding, verify checksums, and record the git SHA in `VERSION`.
2. Ensure migrations run before distributing the app—DJDesk applies them via the bundled launcher when Electron starts, but you can bake them into the bundle if your workflow demands it.
3. Document where installers write user data/logs so downstream teams know how to reset environments.
