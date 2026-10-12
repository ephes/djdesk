Documentation Changelog
=======================

Unreleased
----------

Fixed
~~~~~

- Preserve edited Data Lab notebooks with exclusive numeric-suffix exports and select the new file.

- Atomically claim requested task runs, preserving outcomes on duplicate delivery.

- Stop the whole process group of a workspace command on timeout (``SIGTERM``, then ``SIGKILL``)
  instead of only the direct child, so processes it started no longer keep running.
- Always stop and reap a workspace command and close its output pipe, including when logging its
  output fails; such runs are now recorded as failed with the original error instead of staying
  ``RUNNING``.
- Known limitation: process-group cleanup and output reading for workspace commands are only
  tested on Linux and macOS. The Windows path (``taskkill /T /F``) is untested and only reaches
  descendants while the direct child is still running.

Added
~~~~~

- Introduced the Inspector workspace explorer with workspaces, scan jobs, task presets, curated
  doc links, services, and tests so the desktop shell can analyze and orchestrate local Django
  projects from the Electron UI.
- Built an interactive Inspector dashboard experience with schema visualization assets,
  templatetags, and wizard/dashboard templates that surface curated insights directly in the app.
- Added a read-only ``ci`` GitHub Actions workflow that runs Ruff, the Django test suite with
  ``djdesk.settings.test``, and the Electron guard tests on every push to ``main`` and every pull
  request, plus a matching ``just ci`` recipe. Packaging stays in the manual ``electron-desktop``
  workflow.

Security
~~~~~~~~

- Workspace commands are validated as the exact argv that is executed against a per-command
  allowlist of arguments. ``dumpdata --output``/``-o`` and every other option outside the
  read-only set (``--settings``, ``--pythonpath``, ``diffsettings --default``, abbreviations,
  grouped short options) are refused, so presets can no longer overwrite files.
- The Electron shell now keeps the privileged window on the exact local Django origin: foreign
  top-level navigations and redirects are cancelled, new windows and ``<webview>`` tags are
  denied, and external ``http(s)`` links open in the system browser instead.
- ``djdeskNative.openExternal`` only dispatches ``http:``/``https:`` URLs without credentials and,
  like ``djdeskNative.notify``, only answers the top-level frame showing the DJDesk origin, so
  ``file:`` and custom protocol handlers can no longer be launched from page content.

Changed
~~~~~~~

- The bundled Electron launcher now applies Django migrations automatically during packaging so
  the embedded SQLite schema stays current when distributing desktop builds.
- Linux Electron builds are temporarily disabled in CI while the packaging pipeline is stabilized,
  preventing broken artifacts from being published.
- Task runner presets now execute the real ``INSPECTOR_SAFE_COMMANDS`` through ``django-tasks``,
  stream stdout/stderr into the assistant drawer, and enforce the new
  ``DJDESK_INSPECTOR_TASK_TIMEOUT`` guardrail so automation steps stay deterministic on desktop
  builds.
- Workspace onboarding now blocks duplicate/sloppy project paths, retries slug generation on rare
  collisions, indexes ``project_path`` lookups, enforces SAFE command presets, and batches task log
  writes so long-running automations stay responsive inside the Inspector UI.

Documentation
~~~~~~~~~~~~~

- Expanded the tutorial and reference material with the full Inspector flow (tutorial chapters,
  concepts, architecture, and API/WebSocket docs) so the documentation matches the product surface.
- Rewrote the Electron packaging guide and ensured the Read the Docs requirements install
  ``django-tasks`` so published instructions stay accurate with the codebase.
- Replaced the multi-file Stage tutorial with a single “Integrating Django with Electron” guide,
  added a migration helper page, and updated the docs navigation/reference pages accordingly so
  readers follow one cohesive integration story.

0.1.0 (2025-11-15)
------------------

Highlights
~~~~~~~~~~

- Bootstrapped the Django + Electron project structure with uv-powered installation, ``just``
  recipes for dev/lint/test, and manage.py-based test execution to streamline contributor
  onboarding.
- Added the initial packaging toolchain: bundled the Django payload and python-build-standalone
  interpreter, enforced explicit settings modules, and introduced cross-platform build recipes plus
  verification workflows.
- Delivered GitHub Actions and ``just`` helpers for downloading per-OS artifacts, cleaning build
  outputs, and triggering manual desktop workflow runs, making it easier to share distributable
  installers.
- Set up the Sphinx documentation scaffold, README improvements (including RTD links), and
  contributor guidance (CLAUDE/agent instructions) to support future documentation releases.
