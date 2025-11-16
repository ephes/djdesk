Core Concepts
=============

DJDesk is a local-first "project inspector" for Django applications. Every tutorial
chapter mirrors a concrete capability inside the Electron shell so contributors can
inspect their own projects or simply follow along with the seeded workspace.

Why Electron?
-------------

* **Local file system access** — drag a project folder into the shell and the wizard
  auto-fills the path. The same feature would be blocked in a browser sandbox.
* **System automation** — ``django-tasks`` executes pre-approved commands on your own
  machine. Electron can spawn ``python`` or ``pytest`` without jumping through server
  hoops.
* **Offline parity** — the SQLite database, docs bundle, and static assets ship with
  the binary so the entire tutorial works without a network connection.

Data model
----------

``Workspace`` instances capture metadata about an inspected project (path, Python and
Django versions, docs URL, schema insights, etc.). ``ScanJob`` rows represent schema,
migration, log, and fixture scans. ``TaskPreset`` + ``WorkspaceTaskRun`` records power
the assistant drawer and safe automation story.

Workflow summary
----------------

1. Run ``just electron-start`` to boot the packaged Django server.
2. Import a project via the wizard to seed scans and metadata.
3. Observe schema, migration, activity, and log data update in real time.
4. Exercise native affordances such as drag/drop, offline indicator, and notifications.
5. Dispatch safe commands through ``django-tasks`` and monitor the assistant drawer.
