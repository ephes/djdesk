Documentation Changelog
=======================

Unreleased
----------

Fixed
~~~~~

- Omit notebook filenames outside the viewer's ASCII slug grammar from listings without
  changing their bytes, so editor renames cannot break the dashboard or status feed.
- Publish complete, flushed notebook exports atomically without clobbering edited or
  concurrent exports. Keep staging files outside the listing suffix, clean owned staging
  on ordinary failures, and leave hidden, unlisted staging after process interruption.
- Recognize PostgreSQL workspace slug collisions through SQLSTATE/table/constraint
  diagnostics and restrict SQLite retries to its exact slug constraint; retain five
  attempts and propagate unrelated integrity errors. Add opt-in local PostgreSQL tests.

- Load only the selected workspace's four recent scans and five task runs on the
  dashboard, without task logs or other workspaces' metadata. Use a stable timestamp/PK
  order in both the initial page and status polling.
- Admit renderer status/severity/log-level classes through explicit allowlists;
  escape dynamic attribute values and bound progress percentages so stored metadata
  cannot create HTML elements or attributes during polling.

- Dispatch approved task requests after commit, preserving worker outcomes if enqueue
  raises. Rolled-back requests start no command; deferred responses remain requested;
  an immediate unclaimed enqueue failure still returns the dispatch error.
- Add explicit bounded ``recover_task_runs`` recovery for operator-confirmed abandoned
  running tasks after worker shutdown, retaining logs and never replaying commands.

- Refuse equal or nested offline docs source/destination trees, including symlink and
  case aliases, before changing files. Stage provenance metadata with the HTML so a failed
  manifest write leaves the previous complete generation intact.

- Restore the previous offline docs bundle on promotion failure and report a recovery
  copy if restoration fails. Give concurrent publishers unique staging/backup paths
  and a bounded same-destination writer lock, preserving other publishers' files.

- Align Electron setup documentation with the implemented standalone Python bundle
  and Node.js >=22.12.0 requirement; clarify the system-Python development fallback.

- Stage forced sample refreshes before replacing the existing tree, restoring the old sample
  on publication failure and retaining a recovery copy if rollback fails.
- Correct settings-template braces and indentation so the sample generator loads and emits
  valid Python settings.
- Show a bounded HTTP 422 repair page for malformed edited notebooks without changing the file.

- Preserve edited Data Lab notebooks with exclusive numeric-suffix exports and select the new file.

- Atomically claim requested task runs, preserving outcomes on duplicate delivery.

- Stop the whole process group of a workspace command on timeout (``SIGTERM``, then ``SIGKILL``)
  instead of only the direct child, so processes it started no longer keep running.
- Always stop and reap a workspace command and close its output pipe, including when logging its
  output fails; such runs are now recorded as failed with the original error instead of staying
  ``RUNNING``.
- Bound workspace command output and the retained task log by bytes
  (``DJDESK_INSPECTOR_COMMAND_OUTPUT_BYTES``, ``DJDESK_INSPECTOR_TASK_LOG_BYTES``, 1 MiB each).
  A command that floods output, including as one huge line, is stopped and its run fails with
  ``output_limited`` instead of growing memory and the stored log without limit. A log that fits
  the budget is never truncated, a command is not started when the budget cannot hold its first
  log entry, and the log recorded for an unexpected failure stays within the budget too.
- Known limitation: process-group cleanup and output reading for workspace commands are only
  tested on Linux and macOS. The Windows path (``taskkill /T /F``) is untested and only reaches
  descendants while the direct child is still running.

Added
~~~~~

- Exercise generated bundle launchers, runtime dependency selection, collectstatic
  paths and interpreter/Django verification in synthetic build-contract tests.
  Normal PR CI installs only runtime npm dependencies for these tests, with no
  Electron installation or interpreter downloads. Local test recipes reuse
  installed modules and bootstrap runtime dependencies only when missing.

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

- Anchor notebook export creation, publication and owned-inode cleanup to the same
  no-follow directory descriptor used by readers. Refuse invalid workspace directory
  names and fail closed before writes where these export facilities are unavailable
  (including Windows); exported files have owner-only permissions.

- Keep the sandboxed preload bridge compatible with current Electron APIs, using
  ``webUtils.getPathForFile`` for drops without importing unsupported Node modules.
- Refresh Electron to >=45.2.3, electron-builder to >=26.18.0 and TOML to >=5.0.1.
  Targeted Electron/tar findings are removed; six moderate transitive minimatch
  audit entries remain in builder (GHSA-g3hv-75wg-m249), without ignores or overrides.
- Publish interpreter download caches only after SHA256 verification, with a two minute
  deadline, 256 MiB ceiling, five HTTPS redirects and automatic partial cleanup/retry.

- Refresh Django to the supported 5.2.19 security floor and sqlparse to >=0.6.0.
- Audit the frozen production lock in read-only CI with pinned pip-audit;
  ``just audit`` runs the same failing-on-findings check locally.
- Add a read-only unsigned unpacked-package and shell/preload smoke workflow with
  a synthetic bundled database and temporary profile; no artifacts are published.

- Restrict Data Lab listings and reads to regular exports in the workspace directory; refuse
  notebook and workspace-directory symlinks, including links within the export root.

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
