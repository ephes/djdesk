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

    just install            # uv sync + editable djdesk install
    just electron-install   # npm install inside ./electron
    just electron-start     # boots Django + Electron together

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

Behind the scenes ``scripts/fetch_samples.py`` writes the ``django-polls``, ``djdesk``
(Atlas Telemetry), and ``generated`` projects into ``INSPECTOR_SAMPLE_ROOT``
(defaults to ``sample_projects/``). Runtime ``content.py`` and the frozen historical
seed resolve sample paths from this setting. Override ``DJDESK_SAMPLE_ROOT`` if
you prefer to stash the bundles elsewhere. See :doc:`../migrations` before
upgrading, repairing or rolling back an inspector database.

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

Before replacing the existing bundle, the publisher checks that the staged index,
search index, and required Furo and project assets are nonempty files. If the check
fails, rebuild with ``just docs-html`` and retry with the rebuilt HTML directory.

If a publisher is interrupted, the next invocation uses the sibling recovery receipt
to restore a verified backup or keep the completed replacement before checking the
new input. Retained paths are reported. Stop publishers before inspecting ambiguous
copies or an invalid receipt.

``DashboardView`` checks the same required members as the publisher before choosing
``/docs/offline/`` for the drawer. The drawer uses ``INSPECTOR_DOCS_BASE_URL`` if a
required file is unavailable, unsafe or empty. An individual missing asset still
returns HTTP 404.
With the bundle active, links under the configured docs base can map to local
pages. Links whose destinations remain external still use the external link policy.

.. _guide-safe-automation:

Wire safe automation hooks
--------------------------

Objective
~~~~~~~~~

Demonstrate how Electron can launch vetted Django commands without turning into a general-purpose shell. DJDesk uses ``django-tasks`` to orchestrate pre-approved runs from the assistant drawer.

Flow summary
~~~~~~~~~~~~

1. ``TaskPreset`` rows define the allowlisted commands (label, key, docs URL, icon, confirmation requirement).
2. The assistant drawer posts to ``POST /api/task-runs/`` with ``workspace``, ``preset``,
   optional note, and ``confirm_safe``. It sends ``X-DJDesk-Task-Response: receipt``
   for a compact receipt.
3. ``execute_workspace_task`` (decorated with ``@django_tasks.task``) simulates work, streams log lines, and updates ``WorkspaceTaskRun`` rows.
4. ``app.js`` displays the saved task ID and log link from the submission receipt,
   then requests fresh workspace status through the poller. Poll results update
   the dashboard and fire desktop notifications when runs finish.

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

1. The sidebar exposes a **Data Lab** panel (currently gated behind the legacy ``DJDESK_FLAG_STAGE_5`` feature flag).
2. Users pick a notebook template (schema audit, log study, etc.).
3. The export endpoint writes a complete ``.ipynb`` file into ``INSPECTOR_DATA_LAB_ROOT/<slug>`` and surfaces it in the panel. Repeated exports choose ``-2``, ``-3`` and subsequent free suffixes, preserving edits.
4. Selecting an export opens a static HTML preview rendered by ``inspector/data_lab.py``. Live kernels remain optional—``--with-data-lab`` installs ``jupyter_server`` and enforces the same SAFE command allowlist.

Built-in exports include stable, notebook-local cell IDs for notebook format 4.5.
The preview still accepts earlier notebooks without IDs. Markdown paragraphs
and the HTML budget follow the same line breaks for LF, CRLF and standalone CR.
The text budget counts the original source bytes. Code and output text retain
their line endings.

Filename stems must contain only ASCII letters, digits, underscores and hyphens, with
at least one character. Other names remain untouched but are omitted from listings;
rename them to a supported stem to make them visible in DJDesk.

The export form owns one POST at a time. A total 30-second deadline covers response
headers and body consumption, aborting the request and restoring controls on timeout.
Late acknowledgements cannot select a notebook or announce success. A lost response
may hide an already completed export: inspect the listing or export directory before
deliberately retrying, since a retry creates another file. There is no automatic retry.
Success and error replies share the Inspector's ``DJDESK_INSPECTOR_RESPONSE_BYTES``
ceiling before JSON parsing, even with missing or inaccurate ``Content-Length``.
Overflow cancels the read, preserves notebook selection and reports the same uncertain
outcome. Small HTTP error diagnostics remain visible in the form's notification. A
parsed 4xx reply is a definite refusal and omits the uncertainty guidance.

Exports fsync an owner-only hidden ``.notebook-*.tmp`` file, then publish a hard link
atomically without overwriting another file. Polling sees only complete exports.
The opened workspace directory is fsynced after publication before reporting success.
On macOS both syncs also use ``F_FULLFSYNC`` so the drive cache is flushed.
A directory-sync failure returns HTTP 500 naming the already published notebook, whose
durability is uncertain. The complete visible export and existing notebooks are preserved.
Inspect the directory before exporting again; retrying creates another file, and a power
loss before a successful directory sync may lose the new name.
All export operations use the same opened workspace directory, so a directory rename
cannot redirect writing or cleanup. The renamed directory may retain the export.
After both directory syncs succeed, staging cleanup or descriptor close failures
still return the notebook's HTTP 201 receipt and log the affected path on the server.
Cleanup failures and interrupted exports can leave hidden temporary files. Inspect
and remove leftovers manually with exporters stopped.
Systems without directory-relative no-follow opens and hard-link publication (including
Windows) refuse exports before writing, with HTTP 400. The filesystem must support
hard links; the portable reader remains available for existing notebooks. Filesystem
failures during publication return an HTTP 500 JSON error and leave no partial export.

Listings and previews accept regular notebook files directly inside the workspace export
directory. Notebook symlinks, even internal ones, and symlinked workspace export directories
are refused. On systems supporting directory-relative opens and no-follow flags, validation
is bound to the opened descriptors. The portable fallback checks file identity around opening.
The inventory scans up to 512 directory entries plus one lookahead and lists up to 50
regular notebooks in filename order within that window. Directories and symlinks count
toward the scan bound.
The drawer shows a notice for an incomplete inventory and merges the available entries
into its current list. A complete refresh replaces the list and removes absent entries.
The notebook must contain a ``cells`` list. An empty list is valid.
Supported cell types are ``markdown``, ``code`` and ``raw``. Raw cells display escaped,
preformatted source with a **Raw** label. A missing or unsupported cell type returns
the HTTP 422 repair page for the entire preview.
Malformed UTF-8, JSON, cells or text outputs produce an HTTP 422 repair page with no notebook
content or parser traceback. Repair the export in your editor and reload. The preview does
not rewrite or execute it, and rendered source and output text remain HTML-escaped.
The preview validates each source/output string and measures its byte cost with a single
UTF-8 encoding. For Markdown, the text limit counts original line endings and the HTML
limit uses normalized line breaks.
Each preview reads at most the opened file's initial byte size plus one and compares
descriptor size, nanosecond mtime and ctime around the read before decoding. Changed
generations are reopened, with three attempts at most; continued in-place editing returns
HTTP 404 (unavailable). Finish saving and reload. An atomic replacement can yield the
complete opened generation or a retried new generation. Detection relies on the filesystem
exposing content edits through size or timestamps.

Apply this to your project
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Decide whether rich exports are static (HTML snapshots) or interactive (kernels, sandboxes).
2. Store artifacts under a repo-ignored directory (e.g., ``var/data_lab/``) so contributors can reset between runs.
3. Reuse your task orchestration pipeline so notebook exports appear alongside other background jobs.

.. _guide-package:

Package and distribute
----------------------

Objective
~~~~~~~~~

Bundle the Django payload with Electron so contributors can install/run DJDesk without managing Python. This section summarizes the workflow and points to :doc:`../electron` for exhaustive reference.

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

1. Build in uniquely owned staging, verify checksums and the complete runtime, then publish with restoration of the previous payload on failure. Reserve the destination against concurrent builders and record the git SHA in ``VERSION``. See :doc:`../electron` for recovery and cache limits.
2. Ensure migrations run before distributing the app—DJDesk applies them via the bundled launcher when Electron starts, but you can bake them into the bundle if your workflow demands it.
3. Document where installers write user data/logs so downstream teams know how to reset environments.
