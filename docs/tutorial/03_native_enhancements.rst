Stage 3 — Native Enhancements
=============================

Stage 3 highlights the features that do not exist in the hosted version of DJDesk and
therefore prove why Electron wraps Django.

Drag & drop wizard priming
--------------------------

The left rail exposes a ``.workspace-dropzone`` element. When you drop a folder in the
Electron shell the preload script forwards ``File.path`` to the renderer, which then
updates the wizard form via ``localStorage``. In a normal browser we only receive the
filename, so the UI simply displays the dropped name and nudges you to paste the path
manually. Capture both flows in the tutorial to emphasize the graceful degradation.

System notifications
--------------------

``inspector/static/inspector/app.js`` listens for polling responses and fires a system
notification when a task transitions to ``succeeded`` or ``failed``. The Web
Notifications API works inside Electron without any additional code—Electron bridges
it to the native notification center automatically. Mention the permission prompt the
first time the renderer loads and show where the notification text is configured.

Offline-ready indicator
-----------------------

The bottom status bar has ``data-offline-indicator`` that flips between "Offline-ready"
and "Synced" when ``navigator.onLine`` changes. Because the tutorials bundle docs and
the SQLite database locally, you can literally disconnect from the network and keep
using the inspector. Include a quick checklist for QA:

1. Launch ``just electron-start`` and wait for the dashboard.
2. Disable Wi-Fi.
3. Confirm the scan queue, task history, and doc links still render (the docs entry
   opens the hosted copy if you are online, but the shell can also embed a static HTML
   export in future releases).

Shell automations
-----------------

This stage also introduces the ``SAFE_COMMANDS`` allowlist referenced in the PRD. The
commands are encapsulated in :mod:`djdesk.inspector.tasks` so Electron can later spawn
native processes (e.g., ``pytest``) and stream logs back to the assistant drawer. The
docs should remind readers that DJDesk never executes arbitrary shell commands and the
confirmation checkbox exists to reinforce that contract.
