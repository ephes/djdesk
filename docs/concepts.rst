Why Electron + Django?
======================

DJDesk is intentionally positioned as a reference implementation for embedding a Django project inside an Electron shell. Use these concepts to decide whether the approach fits your own workflow before diving into the :doc:`guide/integrating_django_with_electron`.

Motivation
----------

* **Local file system access** — desktop shells can read/write real paths, so flows like “drop a project folder into the wizard” are trivial. Browsers only expose filenames.
* **System automation** — utilities such as ``django-tasks`` can spawn ``python``/``pytest`` locally without proxy servers or additional authentication.
* **Offline parity** — installers ship SQLite, docs, and static assets, so the entire walkthrough works on a plane or in an isolated lab.

When Electron helps
-------------------

* Internal tooling for developers/analysts who cannot manage Python environments but still need to inspect Django projects.
* Demos or workshops where bandwidth is unreliable yet you want a polished desktop experience.
* Scenarios that benefit from native UX flourishes (drag/drop, notifications, menu bar entries) layered on top of familiar Django templates.

When to reconsider
------------------

* Strict bundle-size budgets (<50 MB) or resource-constrained hardware that struggles with Chromium + Django processes.
* Deployments already successful as web apps, or use cases requiring multi-user access over the network.
* Teams leaning toward lighter shells (e.g., Tauri) or Python-only bundlers (PyInstaller) who do not need Electron’s ecosystem.

Apply these patterns
--------------------

The :doc:`guide/integrating_django_with_electron` breaks integration into six steps. For quick reference:

1. **Boot Django from Electron** – reuse the ``run_django.py`` & interpreter-detection logic.
2. **Render Django UI** – keep server-rendered templates and expose a consolidated status endpoint.
3. **Add desktop touches** *(optional)* – drag/drop, offline indicators, notifications.
4. **Wire safe automation** – allowlisted commands via ``django-tasks`` or your own queue.
5. **Ship rich exports** *(optional)* – export notebooks or artifacts from the packaged app.
6. **Package & distribute** – bundle python-build-standalone + Django code for installers.

Every DJDesk page ties back to those steps so you can copy only the parts you need.

The task runner now shells out to the inspected workspace for every preset. Only commands that match
``INSPECTOR_SAFE_COMMANDS`` are accepted, output is streamed into ``WorkspaceTaskRun.log``, and each
process is killed after ``DJDESK_INSPECTOR_TASK_TIMEOUT`` seconds (default ``60``). Override the
environment variable ``DJDESK_INSPECTOR_TASK_TIMEOUT`` in packaging scripts if your presets need a
longer window while keeping the read-only contract intact.
