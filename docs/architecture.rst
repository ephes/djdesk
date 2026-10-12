Architecture Overview
=====================

This page complements the :doc:`guide/integrating_django_with_electron` by highlighting how the moving pieces fit together. Use it as a quick reference while adapting DJDesk patterns to your own project.

Process model
-------------

``electron/main.js`` boots Django either via the bundled python-build-standalone interpreter or the system interpreter. Startup flow:

1. Discover an open port with :mod:`get-port`.
2. Spawn ``run_django.py`` (when bundled) or ``manage.py runserver`` (when relying on system Python) with ``DJANGO_SETTINGS_MODULE`` pointing at ``djdesk.settings.local``.
3. Poll ``/`` until Django responds, then load the renderer window.
4. Stream Django stdout/stderr to the terminal for quick debugging.

``preload.cjs`` now exposes ``window.djdeskNative`` so drag/drop paths, desktop notifications, and ``shell.openExternal`` deep links are mediated through a single bridge. The preload layer dispatches ``djdesk:workspace-drop`` events with sanitized file paths, stages the wizard hint under ``djdesk.wizard.projectPath``, and proxies notification/deep-link requests back to ``electron/main.js`` via IPC.

Shell boundary
--------------

The window that carries the preload bridge must only ever display the local Django server.
``electron/boundary-guards.js`` enforces this in the main process:

* **Trusted origin.** The only trusted origin is the exact ``http://127.0.0.1:<port>`` the shell
  started Django on. A different port, ``localhost``, or ``https`` does not match.
* **Navigation and redirects.** Top-level navigations and redirects that leave the trusted origin
  are cancelled. A cancelled top-level ``http(s)`` link is handed to the system browser instead;
  redirects are only cancelled. Sub frames (the docs drawer ``<iframe>``) may load the trusted
  origin or plain ``http(s)`` pages, never ``file:``, ``data:``, ``javascript:``, or custom schemes.
* **New windows.** ``window.open`` and ``target="_blank"`` never create an Electron window.
  ``http(s)`` targets (local DJDesk pages included) open in the system browser; everything else
  is dropped. ``<webview>``
  attachment is blocked.
* **IPC senders.** ``djdesk:open-external`` and ``djdesk:notify`` only answer the top-level frame
  while it displays the trusted origin. Sub frames and foreign pages get ``false``.
* **URL dispatch.** ``shell.openExternal`` only receives ``http:``/``https:`` URLs without embedded
  credentials, so ``file:`` paths and custom protocol handlers cannot be launched from the page.

``just electron-test`` runs the guard tests under ``electron/test/`` with ``node --test``.

Renderer data flow
------------------

* The dashboard is server-rendered; ``DashboardView`` populates ``workspace``, ``doc_links``, ``task_presets``, and other context so the first paint always has meaningful data.
* ``inspector/static/inspector/app.js`` polls ``/api/workspaces/<slug>/status/`` to keep the scan queue, insights, schema graph, and log stream updated. Submitting the assistant form returns the same payload so the UI can refresh immediately.
* ``django-tasks`` executes commands synchronously via ``ImmediateBackend``. Swapping in Celery/Redis later will not change the REST payload or UI contract because the ``TaskPreset`` + ``WorkspaceTaskRun`` models stay stable.

Native hooks
------------

* **Drag/drop wizard priming** – the preload script fires ``djdesk:workspace-drop`` events whenever a region tagged with ``data-dropzone`` receives a file drop and stores the normalized path under ``djdesk.wizard.projectPath``. ``app.js`` listens for the event to update the sidebar dropzone, while the wizard form reads/clears the staged path on load.
* **Notifications** – ``app.js`` calls ``window.djdeskNative.notify`` when task runs finish so Electron's main process can raise an OS notification. Browsers fall back to the Web Notifications API when the bridge is absent.
* **Doc deep links** – links annotated with ``data-doc-link`` still open the in-app drawer, but they also call ``window.djdeskNative.openExternal`` so the system browser mirrors the same Read the Docs location. Only ``http(s)`` URLs are dispatched (see *Shell boundary*).
* **Offline indicator** – templates expose ``data-offline-indicator`` so ``navigator.onLine`` updates the status bar.

Frontend assets
---------------

No JS bundler is required for the reference build—``inspector/static/inspector/app.css`` and ``app.js`` are served as-is. This keeps contribution friction low and mirrors how many Django teams work today. You can always migrate to Vite or another bundler after validating the Electron integration.
