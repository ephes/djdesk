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

  Status and task-detail reads stream at most ``INSPECTOR_RESPONSE_BYTES`` of
  response bytes before JSON parsing (``DJDESK_INSPECTOR_RESPONSE_BYTES``; default
  8 MiB plus five task logs at six bytes per retained log byte, the worst-case JSON
  escape of a control character). The dashboard receives the ceiling through its
  ``data-response-bytes`` attribute; without it the reader uses 8 MiB.
  Content-Length can reject early but never replaces the streamed byte count.
  Overflow cancels the reader and preserves the displayed status and task log;
  later polls or a deliberate drawer reload can accept a smaller response.
  This limit applies to decompressed response bytes, including UTF-8 characters
  split across chunks. The separate POST handlers retain their existing contract.

  Schema polling compares canonical rendered element data (independent of object
  keys and model/connection ordering). Identical responses keep the live graph,
  layout, pan and zoom. A changed schema is staged in a hidden sibling container:
  the previous elements are copied, then additions, removals and changed data are
  applied through Cytoscape's public API. A synchronous layout completes before
  installation, retaining the live viewport. Constructor or layout errors discard
  staging and keep the previous graph; the next status update can retry.
  Ready, tooltip and debounced viewport persistence callbacks belong to their
  graph instance, so retired graphs cannot affect the replacement.
* ``django-tasks`` executes commands synchronously via ``ImmediateBackend``. Swapping in Celery/Redis later will not change the REST payload or UI contract because the ``TaskPreset`` + ``WorkspaceTaskRun`` models stay stable.

  Command output framing scans each decoded chunk once and joins each completed
  or final partial line once. Long newline-free output therefore takes linear
  framing work within the existing command byte budget. Incremental decoding,
  CRLF stripping, empty lines and callback/result semantics remain the same.

  The Django admin exposes task runs as read-only execution history, even for
  superusers. It permits viewing, filtering and searching, but no add, change or
  delete operations. Start a new command through the inspector's confirmed task
  form. After a crash, stop the owning workers and command trees before using
  ``recover_task_runs --confirm-workers-stopped --reason ... --run-id ...``
  against their database (``--database alias``). Recovery preserves the log and
  retires the worker claim; retrying requires a new confirmed request.

  Workspace and task-preset deletion, including ``delete_selected`` and ORM bulk
  deletion, preserves the entire selected set while any related run is requested
  or running. A ``pre_delete`` guard checks on the deletion database inside the
  writer transaction before any row is deleted. PostgreSQL locks the parent to
  fence concurrent run insertion; SQLite serializes writers and may refuse a
  competing writer with a database-lock error. The admin also shows protected
  runs on confirmation and rechecks when the confirmation is submitted.
  Deletion does not cancel subprocesses: wait for settlement or stop the owning
  workers/command trees and explicitly recover abandoned running attempts.
  Requested runs must settle before deletion; recovery accepts only running runs.
  Terminal history (succeeded, failed, cancelled) cascades when its workspace or
  preset is deleted, so export any required records before confirming deletion.

Native hooks
------------

* **Drag/drop wizard priming** – the preload script fires ``djdesk:workspace-drop`` events whenever a region tagged with ``data-dropzone`` receives a file drop and stores the native path under ``djdesk.wizard.projectPath``. ``app.js`` listens for the event to update the sidebar dropzone, while the wizard form reads/clears the staged path on load.
* **Notifications** – ``app.js`` calls ``window.djdeskNative.notify`` when task runs finish so Electron's main process can raise an OS notification. Browsers fall back to the Web Notifications API when the bridge is absent.
* **Doc deep links** – links annotated with ``data-doc-link`` still open the in-app drawer, but they also call ``window.djdeskNative.openExternal`` so the system browser mirrors the same Read the Docs location. Only ``http(s)`` URLs are dispatched (see *Shell boundary*).
* **Offline indicator** – templates expose ``data-offline-indicator`` so ``navigator.onLine`` updates the status bar.

Frontend assets
---------------

No JS bundler is required for the reference build—``inspector/static/inspector/app.css`` and ``app.js`` are served as-is. This keeps contribution friction low and mirrors how many Django teams work today. You can always migrate to Vite or another bundler after validating the Electron integration.

Workspace read generations
--------------------------

The initial dashboard and status payload reload the selected workspace and eagerly
load its four scans and five task runs on the same database alias inside one short
snapshot, even when a router normally sends history reads to a replica. SQLite
uses a read transaction; PostgreSQL uses read-only repeatable read (an existing outer
transaction must already be repeatable read or serializable). Other backends are
refused. Rendering, JSON formatting, documentation links and notebook filesystem
inventory happen afterward; those external resources are not part of the scan snapshot.
The initial page still defers task logs and scan details.
