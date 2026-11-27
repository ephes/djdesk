REST Endpoints
==============

All endpoints documented below live in ``djdesk.inspector.urls``. The reference build
ships with CSRF protection enabled, so remember to include the ``csrftoken`` value
when POSTing from custom scripts or sketches.

Dynamic inspector pages and JSON, including missing-resource, validation, method and
server error responses, carry Django's ``private, no-store`` cache handling. Offline
documentation files have an independent cache policy. They use owned regular-file
handles and refuse changed paths or special files with HTTP 404; response close releases
the descriptor. Systems without nonblocking file opens use the online docs instead.

``GET /api/workspaces/<slug>/status/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns the data needed to render the dashboard. Example response (trimmed):

.. code-block:: json

    {
      "workspace": "atlas-telemetry-studio",
      "metadata_needs_repair": false,
      "insights": [
        {"title": "Pending migrations", "value": "3", "delta": "-1 vs last sync"}
      ],
      "apps": [
        {"label": "catalog.datasets", "pending_migrations": 1, "models": 6},
        {"label": "ops.telemetry", "pending_migrations": 0, "models": 9}
      ],
      "schema": {
        "nodes": [
          {"name": "Dataset", "fields": ["id", "slug"], "relations": ["Owner", "Snapshot"]}
        ]
      },
      "log_excerpt": [
        {"timestamp": "15:22:01", "level": "info", "message": "Running django check..."}
      ],
      "scans": [
        {"id": 1, "kind": "schema", "status": "completed", "progress": 100, "summary": "Discovered 24 models"},
        {"id": 2, "kind": "migrations", "status": "running", "progress": 55, "summary": "Reviewing drift"}
      ],
      "tasks": [
        {"id": 5, "label": "Diff migrations", "status": "running", "progress": 40}
      ],
      "doc_links": [
        {"title": "Integration guide — Render Django UI", "url": "https://djdesk.readthedocs.io/en/latest/guide/integrating_django_with_electron.html#guide-render-django"}
      ]
    }

The Electron renderer polls this endpoint every few seconds. You can also hit it from
``curl`` while following the tutorial to see the raw data.

Polling retrieves log excerpts for the latest four scans and logs for the latest
five task runs. The history queries defer scan ``details`` and task-run ``metadata``.
The individual task-run endpoint still returns its metadata.

Workspace metadata edits require a JSON object. For existing workspaces,
``metadata_needs_repair`` is true when the saved metadata or one of its dashboard
sections has an invalid shape. Affected sections use their usual empty states,
while valid sections remain visible. The dashboard shows a notice to review the
saved metadata in Django admin. Reads preserve the stored JSON.

The ``data_lab.notebooks`` inventory offers at most 50 supported notebook filenames,
sorted within a window of at most 512 directory entries (plus one lookahead).
Hidden/unrelated entries consume this scan budget too. The window comes from filesystem
enumeration order; it is not a global alphabetical page. Each backend caches up to 32
directory generations by path, device/inode, mtime and ctime, but only once those
timestamps are more than three seconds old (coarse filesystem clocks can miss a same-tick
change); more recent directories are rescanned. Unchanged polls reuse the window,
reopening at most 50 selected files for current admission and modification times. A scan
overlapping a directory change is retried up to three times.
``data_lab.notebooks_incomplete`` is true if either limit was reached or some entries
could not be admitted. All exports remain on disk and direct viewer URLs still work.
Notebook modification times must be finite and representable as UTC dates in years
1–9999. An entry outside this display domain is omitted and sets
``data_lab.notebooks_incomplete`` without interrupting status polling or hiding valid
siblings. No replacement modification time is invented; a valid epoch time stays exact.
If the workspace export directory is missing, status returns an empty inventory
with ``data_lab.notebooks_incomplete`` set to false. Status and viewer requests
leave the export directories untouched.

``POST /api/workspaces/<slug>/data-lab/export/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Required form field: ``template`` (``schema-audit`` or ``log-study``). A successful
response has status 201 and contains the status payload plus
``data_lab.selected_notebook`` and ``data_lab.selected_notebook_url`` for the exact new
file, even when that file is outside the inventory window. If the optional status
refresh or its serialization fails, HTTP 201 contains only these two ``data_lab``
fields, even if the server cannot log the refresh failure. Existing files are preserved.
The export POST creates missing directories. A missing export root or ancestor is
created and the parent of each missing directory is fsynced before publication. Root
provisioning is serialized within the server process. A failed sync stops publication
and is retried by the next export that needs the pending root or shared ancestor entry.
Exports under independent roots can proceed. Success follows file fsync,
hard-link publication, fsync of the opened workspace directory and fsync of the opened
export root to persist the workspace directory entry. On macOS these syncs also issue
``F_FULLFSYNC`` to flush the drive cache where supported. After both directory
syncs succeed, staging cleanup and descriptor close failures leave the HTTP 201
receipt intact. The server logs a warning with the affected path. A hidden staging
file may remain. Cleanup failures preserve any earlier write or sync failure.
If a directory sync after publication fails, the HTTP 500 JSON response names the
complete published file and reports uncertain durability even if server logging fails.
The export remains visible. Inspect the export directory before exporting again.
Retries create additional files. Fail-closed publication errors (no hard-link support,
a directory swapped for a symlink, a failed staging write) return an HTTP 500 JSON error
with nothing published, also when server logging fails.

The drawer permits one export POST at a time, restores the prior control state after
every outcome and never automatically replays a POST. Manual notebook selection while
the request is pending fences response-driven selection. Export responses update only
Data Lab, leaving workspace status to polling. Polls retain the currently
open notebook. Validate this behavior against the rendered dashboard with
``cd electron && npm run test:drawer`` after installing development npm dependencies
and Chromium (or setting ``CHROME_EXECUTABLE`` to an installed Chrome binary).

Notebook viewer reads compare descriptor size, nanosecond mtime and ctime around a read
bounded to the initial byte size plus one, before UTF-8 and JSON decoding. Changed files
are reopened up to three attempts total. Continued concurrent editing returns HTTP 404;
finish saving and reload. Atomic replacement returns one complete generation. Stable
malformed notebooks retain the HTTP 422 repair response.

``POST /api/task-runs/``
~~~~~~~~~~~~~~~~~~~~~~~~

Queues a preset command for the selected workspace. Required fields:

* ``workspace`` — the workspace slug (``atlas-telemetry-studio``).
* ``preset`` — the task key (``showmigrations``, ``check``, etc.).
* ``confirm_safe`` — must be ``on``/``true`` to acknowledge the read-only contract.

Example ``curl`` request (assuming ``csrftoken`` is stored in ``TOKEN``):

.. code-block:: bash

    curl -H "X-CSRFToken: $TOKEN" \
         -H "Cookie: csrftoken=$TOKEN" \
         -d "workspace=atlas-telemetry-studio" \
         -d "preset=showmigrations" \
         -d "confirm_safe=on" \
         https://localhost:8000/api/task-runs/

The default response mirrors ``GET /api/workspaces/<slug>/status/`` and includes an extra
``task_run`` object with the ID and last known status, progress, and log of the
newly created run. If reading the run after dispatch fails, the receipt uses
the initial ``requested`` state. Read ``GET /api/task-runs/<id>/`` or poll the
workspace status for the current outcome.
If the optional status refresh or its serialization fails, HTTP 201 contains only
``task_run``. The receipt is returned even if the server cannot log the refresh failure.
Send the header ``X-DJDesk-Task-Response: receipt`` to receive HTTP 201 with just
``{"task_run": {"id": 123}}`` (using the saved run ID). This skips building the
workspace snapshot. Validation and dispatch errors use the same HTTP 400
response as the default mode. The Inspector requests this compact receipt,
displays the ID and refreshes workspace status through a separate GET.

``GET /api/task-runs/<id>/``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns an individual run with log output and metadata. Useful for debugging or
building future CLI tooling:

.. code-block:: json

    {
      "id": 5,
      "preset": "showmigrations",
      "label": "Diff migrations",
      "status": "succeeded",
      "progress": 100,
      "log": "[15:21:00] Executing `python manage.py showmigrations`\n[15:21:01] Command finished with exit code 0.",
      "metadata": {
        "command": {
          "raw": "python manage.py showmigrations",
          "safe_prefix": "python manage.py showmigrations",
          "workspace_path": "/Users/example/Projects/atlas",
          "exit_code": 0,
          "duration_seconds": 2.41,
          "output_lines": 12,
          "timed_out": false,
          "output_limited": false
        }
      }
    }

If a command fails (non-zero exit code, timeout, or workspace validation error) the response keeps
the same structure but the ``command`` payload adds an ``error`` string summarizing the root cause.
``output_limited`` is ``true`` when the command was stopped, or not started at all, because its
output or the retained log exceeded the byte budget; such runs always have the status ``failed``.
