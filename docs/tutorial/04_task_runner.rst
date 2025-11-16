Stage 4 — Task Runner & Assistant
=================================

Stage 4 stitches everything together. The assistant drawer now dispatches safe
commands through ``django-tasks`` and mirrors progress back into the UI.

How it works
------------

* ``TaskPreset`` records live in the database and describe the allowlist. Each preset
  references a docs URL, icon, and command string.
* ``TaskRunForm`` powers the drawer form. It uses ``ModelChoiceField`` with
  ``to_field_name`` so the form can accept ``workspace`` slugs and preset keys rather
  than numeric IDs.
* ``execute_workspace_task`` is declared with ``@django_tasks.task``. In the tutorial
  build we intentionally simulate output so the run completes immediately but still
  yields log messages, progress updates, and summary payloads.

Step-by-step
------------

1. Open the assistant drawer, pick a preset (``Diff migrations`` works well for demos),
   and add an optional note.
2. Toggle the confirmation checkbox—this is how we enforce the read-only allowlist.
3. When you submit the form the drawer POSTs to ``POST /api/task-runs/`` with the form
   data. The server enqueues ``execute_workspace_task`` and responds with the updated
   ``workspace_status_payload``.
4. ``app.js`` updates the task history list and stores the latest status per task so
   it can fire a desktop notification whenever a run completes.

Extending the queue
-------------------

Add a new preset via the Django admin or a migration and then register a simulation in
``djdesk.inspector.content.TASK_SIMULATIONS``. Once Celery/Redis support lands you can
swap the ``django_tasks.backends.immediate.ImmediateBackend`` for a distributed
backend without touching the assistant drawer or docs.
