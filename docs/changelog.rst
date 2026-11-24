Documentation Changelog
=======================

Unreleased
----------

Fixed
~~~~~

- Preserve explicitly supplied workspace slugs when a duplicate save is rejected.
  Only automatically generated slugs are retried after a uniqueness collision.
- Apply task completion fields to the worker's run instance only after the
  ownership-checked update succeeds.
- Keep notebook inventory and viewer requests read-only. Only exports provision
  missing notebook directories.
- Sync the export root before acknowledging notebook publication so the workspace
  directory entry is covered. If that sync fails, report uncertain durability and
  retain the published notebook.
- Sync the parent entries of a missing export root before the first export publishes.
  Concurrent exports in the same process wait for that sync, and a failed sync is
  retried by the next export.

- Preserve the previous Electron runtime when moving it to backup reports an
  error after the move, restoring it by identity or retaining the recovery location.
- Recognize the installed Electron runtime by identity after a reported promotion
  error, and retain the backup if an unrelated runtime occupies the destination.
- Extract the bundled interpreter from a bounded, checksum-verified private snapshot
  so a concurrent cache refresh cannot replace tar's input.
- Require the bundled interpreter's version probe to exit successfully within ten
  seconds and report the exact pinned version before installing dependencies.

- Verify that the offline docs index is readable as a regular file before
  advertising the bundle. Use the online docs when the index fails admission.
- Preserve external navigation for docs links whose resolved destination stays
  external while the drawer defaults to the offline bundle.

- Return HTTP 201 with the saved task receipt if the optional workspace refresh
  fails, and keep the inspector panels intact until polling refreshes them.
- Keep the task creation receipt when the backend accepts dispatch but its result
  ID cannot be saved. Log the run ID and preserve the worker's state.

- Publish task command receipts and terminal status in one ownership-checked update,
  while retaining batched logs and explicit recovery for unsettled runs.
- Settle a failed task even when its exception message cannot be formatted. Store
  the exception category and let the original exception reach the task backend.

- Apply the Inspector response-byte ceiling to notebook export success and error
  replies before JSON parsing. Cancel oversized streams, retain selection and bounded
  diagnostics, and require inspection before any deliberate retry.

- Bound notebook export acknowledgement to 30 seconds across request and body reads.
  Abort timed-out requests, restore form controls and ignore late success without
  automatic retries; explain the uncertain outcome and inspection before retrying.
  Parsed 4xx refusals stay definite and omit that guidance.

- Anchor sample publication, recovery, rollback and cleanup to held no-follow directory
  descriptors. Recheck named generations before mutations, refuse replaced namespaces
  and retain recovery data with directory identity diagnostics instead of following
  a swapped parent into another tree. Per-sample locks are created inside the held root
  descriptor and recheck the root name after acquisition.

- Index recent scan and task-run history by workspace, creation time and primary key,
  preserving four/five-row windows and deferred logs without sorting full histories.

- Omit notebook inventory entries with nonfinite or undisplayable modification times
  and report an incomplete inventory, preserving valid siblings and status polling
  without substituting fabricated dates. Keep representable boundary and epoch times.

- Make task execution history read-only in the admin, preserving worker-owned
  claims, outcomes and provenance against stale or forged forms and arbitrary adds.
- Refuse workspace/preset single and bulk deletion while task runs are requested
  or running, including at the ORM writer boundary. Preserve complete selections
  on refusal and document terminal-history cascade and explicit recovery policy.
  Admin cascade of terminal run history requires the run delete permission.
- Let ``recover_task_runs`` settle abandoned requested runs whose message was lost,
  marking them failed as never started so a late delivery cannot claim them.

- Page the dashboard workspace selector in deterministic 50-row windows with name/path
  search, preserving active selections and direct links outside the visible page.

- Keep workspace slug allocation, savepoints and writes on one resolved write alias;
  validate duplicate paths on the instance's write database and preserve independent
  workspace identities across databases.

- Bound notebook view admission by JSON bytes, cell/output counts and aggregate text
  and escaped HTML work before building fragments; refuse oversized views with HTTP 422.
- Return a committed notebook export receipt even if optional status/inventory refresh
  or serialization fails. Keep the drawer's listing/templates until polling refreshes
  them, open the published export and never replay the POST automatically.

- Reject duplicate notebook JSON members and lone surrogates in cell sources/output
  text before rendering; return the repairable HTTP 422 page and preserve edited bytes.

- Keep task dispatch, claims, logs, metadata and outcomes on the saved database alias;
  allow explicit alias selection for worker execution and operator recovery.
- Fence worker publications by their running claim so operator recovery remains
  authoritative even if an old worker returns later, without replaying commands.

- Persist sample swap intents before removing the public name and reconcile interrupted
  generations under the publisher lock. Retain interrupted backups and staging for
  inspection; keep recovered samples for this invocation even with force, and refuse
  changed identities and malformed control files without mutation.
- Sync staged sample files/directories, the intent and rename parents before retiring
  the old backup. Report visible-but-uncertain durability failures without rollback or
  automatic retries; share macOS drive-cache flushing with notebook exports. Unsupported
  directory-sync platforms refuse sample creation/replacement before changing old data.

- Serialize sample publishers with stable native per-target locks (no soft-lock fallback)
  through preflight,
  swap, rollback and cleanup, including an existence recheck for non-force callers.
- Reload dashboard/poll workspace metadata and materialize bounded scan/task histories
  in one SQLite or PostgreSQL read snapshot on the same alias, including with replica
  routers, before rendering or filesystem access.
  An opt-in PostgreSQL test pins the repeatable-read snapshot and the refusal of a
  read-committed outer transaction.

- Fsync the opened Data Lab publication directory after the final no-clobber hard link
  before acknowledging success. A failed directory sync reports the already published
  filename and uncertain durability without deleting complete exports or existing edits.
  Where available (macOS), file and directory syncs also issue ``F_FULLFSYNC``.
- Fence notebook preview reads with descriptor size, mtime and ctime checks before
  decoding, bounding each read to its initial byte size plus one. Retry changed generations
  at most three times, then report unavailable instead of rendering mixed in-place edits;
  preserve stable reads and atomic editor replacements.

- Build Django runtimes in unique staging and verify before publication. Restore the
  last good runtime on failed promotion and retain a named backup if restoration fails.
  Exclusively reserve the destination through build, promotion and cleanup; refuse
  duplicate builders immediately and document interrupted-owner recovery.
- Bound cached interpreter hashing by ordinary-file descriptor admission, byte ceiling
  and the total download deadline; refuse special, oversized and changing entries.
  Verify before allocating extraction scratch and preserve primary extraction/copy
  errors when scratch cleanup also fails.
- Frame command output with linear scanning/joining even for long newline-free output,
  preserving incremental decoding and callbacks. Bound inspector status/task-detail
  JSON reads to ``INSPECTOR_RESPONSE_BYTES`` (derived from the task-log budget and
  worst-case JSON escaping, configurable via ``DJDESK_INSPECTOR_RESPONSE_BYTES``),
  cancel overflow before parsing and retain the last displayed state. Ignore publisher
  leftovers in Git and electron-builder packaging.
  Add synthetic publisher/cache/framing tests and response-reader DOM regressions.

- Bind historical inspector seed, documentation-pane and path migrations to the
  selected database alias. Freeze their tutorial seed inputs independently of
  runtime content and asset helpers. Refuse reversal of seed migration 0002 to
  preserve customized workspaces, scans, presets, documentation links and task history.
- Preflight workspace path migration 0004 before mutation: refuse empty normalized
  paths and duplicate groups with conflicting user data, with bounded repair
  diagnostics. Consolidate only equivalent duplicates, preserving canonical identity
  and every child relationship. Bound projected reads and writes to batches across
  duplicate-group boundaries; document backup, repair and schema rollback behavior.
- Fire pending deferred foreign-key checks after migration 0004 merges duplicates on
  PostgreSQL, so its index creation no longer fails with "pending trigger events".
  The opt-in PostgreSQL test settings now run the historical migration tests too.

- Keep the live schema graph and viewport across unchanged status polls. Stage
  changed schema elements and layout before replacing the view, preserving the
  previous graph on failure and ignoring callbacks from retired instances.
  Add schema lifecycle regressions and a separate real Cytoscape browser fixture.
  A failed schema rebuild during polling no longer marks the dashboard offline or
  skips the Data Lab update, and nodes without a string name no longer break sorting.

- Bind offline documentation reads to verified regular-file descriptors and the admitted
  bundle generation. Refuse swapped paths and special files, close failed constructions,
  and transfer successful handle ownership to the streaming response. Platforms without
  nonblocking file opens fall back to online docs.
- Apply Django's private no-store policy to dynamic inspector pages/JSON and their
  error responses while keeping offline documentation caching independent.
- Bound notebook inventories to 512 directory entries plus one lookahead and 50 results,
  cache unchanged directory generations once their timestamps have settled (recent
  directories are rescanned, scans racing a change are retried), and expose incomplete
  inventories in JSON and the drawer. Preserve direct viewing and select newly exported files outside the window.
- Give the export form one pending POST owner, preserve later manual notebook selections,
  restore controls on every outcome, and prevent export snapshots replacing polled status.
  Add a separate real drawer DOM regression with synthetic notebooks, served from a
  synthetic origin so the CSRF cookie lookup runs as in the app.

- Omit notebook filenames outside the viewer's ASCII slug grammar from listings without
  changing their bytes, so editor renames cannot break the dashboard or status feed.
- Publish complete, flushed notebook exports atomically without clobbering edited or
  concurrent exports. Keep staging files outside the listing suffix, clean owned staging
  on ordinary failures, and leave hidden, unlisted staging after process interruption.
- Report filesystem failures during notebook publication (missing hard-link support,
  a swapped export directory, failed staging writes) as an HTTP 500 JSON error instead
  of an unhandled server error.
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

- Fail ``just test`` and ``just ci`` when models and committed migrations diverge:
  ``tests.test_migration_drift`` runs ``makemigrations --check --dry-run --noinput`` under
  ``djdesk.settings.test`` and proves the check detects a missing migration
  and fails on rename candidates instead of prompting.
  ``just check-migrations`` runs the same check on its own.

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

- Remove native browser dispatch from all popup requests, including remote docs,
  missing referrers and trusted-looking referrers. Deliberate app outbound links
  retain the sender-validated IPC bridge and HTTP(S) allowlist. Extend the installed
  handler regressions and real remote-iframe fixture for popup and IPC dispatch.
  In the shell, Cmd/Ctrl/Shift- and middle-clicked external documentation links open through that
  bridge and local documentation links open in the drawer, instead of becoming
  denied popups. Top-level navigations started by embedded documentation (for
  example ``target="_top"``) are cancelled without system-browser dispatch; only
  navigations started by the trusted top-level page are handed to the browser.

- Install both Electron session permission handlers before loading the window.
  Deny all renderer capability requests/checks, including remote documentation,
  unknown frames and the trusted app; native notifications retain their validated
  IPC path. Add handler wiring tests and a real remote-iframe Electron fixture.

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
  denied, and cancelled top-level ``http(s)`` navigation links open in the system browser instead.
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
