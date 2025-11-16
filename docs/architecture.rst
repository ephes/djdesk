Architecture Overview
=====================

High-level view of how the tutorial build works today.

Process model
-------------

``electron/main.js`` boots Django either via the bundled Python runtime or the
system interpreter. It discovers a free port, spawns ``runserver`` with the local
settings module, waits for ``/`` to respond, and then points the renderer at the
server. ``preload.cjs`` (not shown in code snippets yet) is where we'll wire drag/drop
paths, ``djdesk://`` deep links, and future IPC calls for shell automation.

Data flow
---------

* The left rail and canvas render directly from Django context so Stage 2 screenshots
  are deterministic. ``DashboardView`` adds ``workspace``, ``doc_links``,
  ``task_presets`` and other metadata.
* ``inspector/static/inspector/app.js`` polls ``/api/workspaces/<slug>/status/`` every
  few seconds to keep the scan queue, insights, and log stream up to date. When a user
  submits the assistant form the server responds with the same payload so the client
  can refresh everything without waiting for the next poll.
* ``django-tasks`` runs commands synchronously via the ``ImmediateBackend`` today.
  When we switch to Celery/Redis the ``TaskPreset`` + ``WorkspaceTaskRun`` models stay
  the same—the backend swaps out underneath.

Frontend assets
---------------

The UI intentionally avoids a dedicated JS build step so contributors can tweak
``inspector/static/inspector/app.css`` + ``app.js`` and refresh Electron immediately.
Future iterations can replace the static CSS with a Vite build, but for the tutorial
it is more important to keep the stack approachable.
