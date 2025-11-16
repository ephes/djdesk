REST Endpoints
==============

All endpoints documented below live in ``djdesk.inspector.urls``. The tutorial build
ships with CSRF protection enabled, so remember to include the ``csrftoken`` value
when POSTing from custom scripts or sketches.

``GET /api/workspaces/<slug>/status/``
-------------------------------------

Returns the data needed to render the dashboard. Example response (trimmed):

.. code-block:: json

    {
      "workspace": "atlas-telemetry-studio",
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
      "docs": [
        {"title": "Stage 2 — Project Explorer", "url": "https://djdesk.readthedocs.io/..."}
      ]
    }

The Electron renderer polls this endpoint every few seconds. You can also hit it from
``curl`` while following the tutorial to see the raw data.

``POST /api/task-runs/``
------------------------

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

Response payload mirrors ``GET /api/workspaces/<slug>/status/`` and includes an extra
``task_run`` object with the ID/status/log of the newly created run.

``GET /api/task-runs/<id>/``
----------------------------

Returns an individual run with log output and metadata. Useful for debugging or
building future CLI tooling:

.. code-block:: json

    {
      "id": 5,
      "preset": "showmigrations",
      "label": "Diff migrations",
      "status": "succeeded",
      "progress": 100,
      "log": "[15:21:00] Inspecting migrations\n[15:21:04] Found 3 pending migrations.",
      "metadata": {
        "simulation": {
          "summary": "Pending migrations identified across analytics apps.",
          "result": {"pending": 3, "apps": ["catalog.datasets", "ledger.entries"]}
        }
      }
    }
