## Documentation

Full tutorial + reference lives on Read the Docs: https://djdesk.readthedocs.io/en/latest/

```bash
uv pip install -r docs/requirements.txt  # once per machine
just docs-html                           # build static HTML into docs/_build/html
just docs-serve                          # autoreload while writing
```

Read the Docs config lives in `readthedocs.yml`; `main` publishes to the URL above.

## Development

### Prerequisites

- Python 3.14 or newer (see `pyproject.toml`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task automation (optional but recommended)

### Initial setup

```bash
# Install runtime + dev dependencies into .venv using the lockfile.
# uv installs the djdesk package itself in editable mode, so imports work without PYTHONPATH tweaks.
just install

# Install git hooks so Ruff + hygiene checks run before each commit
uv run pre-commit install
```

The `just install` recipe wraps `uv sync` so dependency installs stay consistent across contributors.

> `uv sync` installs dev dependencies by default. Use `uv sync --no-dev` if you intentionally want a
> leaner environment. Activating `.venv/bin/activate` is optional—`uv run …` automatically executes
> commands inside the managed environment.
>
> Using a different workflow? Run `pip install --editable .` (or `uv pip install -e .`) inside your
> virtualenv so `djdesk` stays importable without modifying `sys.path`.

### Everyday commands

- `just dev` – start the Django development server (`manage.py runserver`) using `djdesk.settings.local`.
- `just install` – install/update dependencies via `uv sync`.
- `just test` – run Django's test suite (`manage.py test tests`) using `djdesk.settings.test`.
- `just lint` – run Ruff’s lint checks across the codebase; use `uv run ruff format .` to auto-format.
- `just ci` – run everything the push/PR workflow runs: locked install with the `dev` extra, Ruff, the Django tests, and the Electron guard tests.
- `just hooks` – run every pre-commit hook against the full codebase.
- `just docs-html` – build the Sphinx documentation (`docs/_build/html`).
- `just docs-bundle` – build docs and copy the HTML bundle into `var/docs_bundle` for offline use.
- `just docs-serve` – live-reload the docs while editing reST/Markdown files.
- `just electron-install` / `just electron-start` – install Electron deps and launch the local shell.
- `just electron-test` – run shell guards and synthetic bundle-contract tests with `node --test`, installing runtime npm modules only if missing (no Electron installation; existing development installs are preserved).
- `just electron-bundle` / `just electron-build-*` – create the python-build-standalone bundle and packaged binaries for each OS.
- `just electron-runs` – list recent GitHub Actions builds; `just electron-workflow-run` triggers the matrix build.
- `just electron-download-latest` / `just electron-download-macos` / `just electron-download-windows` / `just electron-download-linux` / `just electron-download RUN_ID=<id>` – download workflow artifacts (all platforms or per platform); `just electron-clean-artifacts` removes the downloaded zips.
- `just fetch-samples` – materialize sample Django projects under `sample_projects/` for screenshots, tests, and wizard defaults.

### Sample projects & offline docs

The tutorial relies on reproducible sample workspaces so screenshots and wizard defaults stay in sync. Run `just fetch-samples` after cloning (or whenever the bundles change) to populate `sample_projects/` with the `django-polls`, `djdesk`, and `generated` fixtures referenced throughout the inspector. Seed migrations expect these directories to exist, so run the recipe before applying migrations or capturing docs/screenshots.

To replace existing samples, run `uv run python scripts/fetch_samples.py --force`.
This replaces the entire sample directory, including local edits, tutorial state and databases.
Publishers wait up to ten seconds for a stable per-sample `.slug.lock` file and
hold it through preflight, publication, rollback and cleanup. Never delete these lock
files while publishers may be running. Native locks are required; there is no fallback
to lock-file existence markers. A waiting non-force publisher rechecks existence
after taking the lock; force publishers replace complete generations in serial order.
Each replacement is written and synced beside the destination first, along with an
owner-only `.slug.swap.json` intent recording the root, staging and old/new directory
identities. One root descriptor covers the lock, recovery check and publication.
Publication and recovery hold no-follow root/staging directory descriptors;
journal operations, renames, rollback and cleanup use those owners with relative names.
They recheck named generations before changing names or deleting contents. A replaced
root, staging name or generation stops publication and retains owned recovery data;
errors include the recorded path and directory identities if the tree has moved.
The rename parents are synced before the old backup is retired. On macOS,
file and directory syncs also use `F_FULLFSYNC` where the filesystem supports it.
A write failure preserves the old sample; a failed install attempts restoration.
A sync failure after installation reports that the new sample is already visible but
its durability is uncertain, retaining the backup/staging for inspection. Never retry
such errors automatically.

The next invocation (including without `--force`) reconciles an interrupted intent under
the same lock. It restores an identity-matched old backup if the public name is missing,
or accepts the already installed new generation. For a first install with no old sample,
it completes the staged generation. Interrupted staging trees are retained, including
user edits to backups, and the warning names their path. Unknown identities or malformed
intents stop publication for manual inspection without removing any related paths.
A recovered sample is kept for this invocation even with `--force`, so recovery does
not immediately delete saved edits. Inspect it before a separate force replacement.
Stop all publishers before inspecting `.slug-*/backup`, `replacement`, or the journal;
copy out valuable files before manual repair. Do not remove a journal until the public
sample is complete and all retained generations have been accounted for. A crash before
the intent is written can leave an unreferenced staging tree; inspect it manually too.
This is a recoverable swap, so readers can briefly see a missing public name between
renames. Cleanup errors report the installed sample; a leftover backup may be incomplete
only after the new generation has been synced successfully.

Creation/replacement requires no-follow directory opens and directory fsync; unsupported
filesystems and Windows refuse before replacing existing samples. Existing samples can
still be skipped without force. See [sample publication and recovery](docs/sample_projects.rst).

The Electron docs drawer prefers a local Sphinx bundle when it exists. Run `just docs-bundle` to copy `docs/_build/html` into `var/docs_bundle/`; the drawer automatically falls back to https://djdesk.readthedocs.io/ when the offline bundle is missing.

Offline assets are served from verified regular-file handles anchored to the admitted
bundle directory. Directory-relative no-follow opens prevent swapped paths from redirecting
reads; portable readers check directory/file identity around a nonblocking open. A changed
or special file returns HTTP 404. The response owns the handle and releases it on close;
response-construction failures close it immediately. Platforms without nonblocking file
opens (including Windows) use the online docs instead.

Offline docs publishers copy into unique sibling staging directories and wait up to ten
seconds for a same-destination writer lock. A failed promotion restores the previous bundle.
If restoration also fails, the original error reports the retained
`.docs_bundle-publish-*/backup` recovery path beside the destination. With publishers stopped,
move that backup back to the destination before retrying. Never remove the sibling lock file
while publishers may be running. Copying happens outside the lock; successful publications
are serialized, with the last completed publication winning. This is a recoverable swap,
not a crash-atomic directory exchange: a killed publisher can leave the destination absent
and its backup in the reported directory pattern. Cleanup failure after promotion reports an
error with the new bundle installed; a leftover backup may be incomplete.
The publisher uses `filelock>=4.2.1`, included in the normal Python dependencies so
`uv run python scripts/bundle_docs.py` works without selecting a development extra.
Custom `--source` and `--dest` paths must resolve to separate directory trees:
equal directory identities and either tree nested inside the other are refused before
filesystem changes, including symlink and case aliases. The source must be an existing directory.
The publisher writes `bundle.json` (UTC generation time and resolved source path) into
staging before acquiring the lock, so HTML and provenance publish as one generation.
A copy, serialization or manifest write failure preserves the old bundle and its manifest.
Run `uv run python -m unittest tests.test_bundle_docs` for the synthetic publisher
regressions, including concurrent processes and filesystem failure injection.

### Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on every pull request with a read-only token and no secrets:

1. `uv sync --locked --extra dev --extra audit` – install exactly what `uv.lock` pins (the job fails if the lock is stale).
2. `uv run --no-sync python scripts/audit_dependencies.py` – audit the frozen production lock.
3. `uv run ruff check .` – the same lint as `just lint`.
4. `python manage.py test tests` with `DJANGO_SETTINGS_MODULE=djdesk.settings.test` – in-memory SQLite, no real workspace or home database.
5. `npm ci --omit=dev --ignore-scripts` then `npm test` in `electron/` – shell guards and synthetic build-contract tests using an existing Python 3, without installing Electron or downloading an interpreter.

Run `just ci` locally before opening a pull request; a change is only ready for review once both CI jobs are green. Cross-platform packaging stays in the manual `electron-desktop` workflow and is not part of these checks.

### Settings and environments

Before upgrading or rolling back a database, stop its writers and make a restorable
backup. See [historical migration and rollback guidance](docs/migrations.rst):
inspector 0002 is irreversible, and 0004 refuses blank paths or conflicting duplicate
workspace data before mutation. Use the intended `--database` alias for every step.

Settings live in `djdesk/settings/`; choose the appropriate module via `DJANGO_SETTINGS_MODULE`:

- `djdesk.settings.local` (default for `manage.py` and `just dev`): debug on, console email backend, localhost hosts.
- `djdesk.settings.test` (used by `just test`): deterministic key, in-memory SQLite DB, fast password hasher, locmem email.
- `djdesk.settings.production` (default for ASGI/WSGI entry points): expects secrets/hosts/DB config from env vars and enables secure cookies.

Override the module explicitly whenever you need something different, e.g.:

```bash
export DJANGO_SETTINGS_MODULE=djdesk.settings.production
export DJANGO_SECRET_KEY="super-secret"
export DJANGO_ALLOWED_HOSTS="example.com"
uv run python manage.py migrate
```

### Electron desktop shell

The shell prefers the Phase 3 standalone Python bundle built by `just electron-bundle`.
Without a bundle, you can run the development fallback using your local interpreter:

```bash
# 1. Ensure backend deps are installed (from repo root)
uv pip install -e .

# 2. Install the Electron dependencies
cd electron
npm install

# 3. Launch the shell (optionally pin a Python binary)
PYTHON=$(command -v python3.14 || command -v python3) npm start
```

`npm start` starts Django on an open port, waits for it to respond, and then shows the site in an Electron window. Close the window (or press `Ctrl+C` in the terminal) to shut down both Electron and Django.

Queued workspace tasks atomically claim only `REQUESTED` runs. Duplicate delivery
returns the persisted command outcome, or `{skipped: true, status: ...}` while a
run is in progress or has no outcome. It never resets logs or timestamps and
holds no database transaction open while executing a command.
Task dispatch carries the saved run's database alias through claim, logs, metadata,
result ID and terminal writes. Programmatic callers use
`execute_workspace_task.enqueue(run_id, database="alias")` (or `.call` for immediate
execution); omitting `database` selects `default`. Workspace and preset must belong
to the selected database; numeric IDs are local to that alias.

Workspace saves resolve `using` (or the write router) once for slug allocation,
collision-retry savepoints and the final write. Duplicate-path model validation
uses the write router with the workspace instance, so loaded instances retain their
own alias under Django's default routing. For validation before an explicit
`save(using=...)`, configure the write router to select that alias too. Slugs,
paths and numeric IDs are independent between databases; only slug-constraint
collisions are retried, at most five attempts.

The dashboard loads four recent scans and five recent task runs for the selected
workspace only, ordered newest first with the primary key breaking timestamp ties.
Composite workspace/created-at/ID indexes serve these windows without sorting the
workspace's complete history. Migration 0006 builds both indexes on existing history;
allow time for the index build when updating a large database.
The workspace selector loads at most 50 names and paths per page, ordered by name
then primary key, without historical logs or metadata. Use Previous/Next or search
by name/path (at most 120 characters); the URL parameters are `workspace_page`
and `workspace_search`. Search and page navigation preserve the active workspace.
Direct `?workspace=<slug>` links open any registered workspace, including entries
outside the selector page; without a slug, the first workspace in name/ID order opens.
Pages reflect the current inventory, so concurrent imports, deletions or renames may
shift entries between pages. Initial task history omits log bodies.
Both the page and polling reload workspace
metadata and materialize the related history in one short database snapshot before
rendering or notebook filesystem access. SQLite uses a read transaction; PostgreSQL
uses a read-only repeatable-read transaction. An existing PostgreSQL outer transaction
must already use repeatable-read or serializable isolation; other database backends
are unsupported for these snapshots. Notebook inventory and documentation links are
independent of the scan snapshot. Status polling keeps the same four/five windows
and includes those task logs. Renderer status, severity and log-level classes use
supported values with neutral defaults; labels remain escaped text.

Dynamic inspector pages and JSON (including error responses) carry Django's private,
no-store cache policy. Offline documentation assets keep an independent cache policy.

Task requests dispatch only after the transaction saving the run commits; rollback
starts no command. A response inside an outer transaction reports `requested`.
In autocommit, an enqueue error before any worker claimed the run returns HTTP 400.
The immediate backend executes after commit and can return a finished outcome;
the database backend queues the committed ID for workers. An enqueue error marks
only an unclaimed request failed, preserving a worker's running or completed outcome.
Poll task status for the final outcome, including deferred enqueue failures.

After a worker crash, stop all workers owning the selected runs and terminate/check
their command process trees before recovery. Age alone does not establish abandonment.
Inspect the saved run IDs and logs, then explicitly record that ownership check:
`uv run python manage.py recover_task_runs --run-id 123 --run-id 124 --confirm-workers-stopped --reason "Owning workers and commands stopped after crash"`.
Use the same settings as those workers and select their alias with `--database alias`
(default: `default`). The command accepts at most 100
selected requested or running IDs, rejects missing/terminal runs, preserves logs, and
marks them failed with interruption metadata. A `REQUESTED` run whose message was lost
(for example a crash between commit and enqueue) is marked failed with `never_started`
metadata; a late delivery can no longer claim it. A run claimed or settled while the
command runs makes the whole call fail without changes. It trusts the operator's shutdown confirmation;
it cannot detect live workers. Restart workers afterward; duplicate deliveries never
replay these commands. Any desired retry requires a new, explicitly approved request.
Execution history in the Django admin is read-only, including for superusers: runs
cannot be added, edited or deleted there. Start tasks through the inspector's
confirmed task form; use `recover_task_runs` for abandoned requested or running attempts
after the ownership checks above. Admin viewing, status filters and search remain available.
Workspace and task-preset deletion is refused while either has any `REQUESTED` or
`RUNNING` runs. This applies to admin single/bulk deletion and ORM model/queryset
deletion; a blocked bulk operation preserves the entire selected set. The final
check uses the deletion database inside the writer transaction and locks the parent
row on PostgreSQL, where a concurrently inserted run fails its foreign-key check rather
than being deleted silently; conflicting SQLite writers may report a database-lock error.
Deletion never cancels a command. Wait for settlement, or stop the owning workers
and command trees and explicitly recover abandoned requested or running runs first.
Once all related runs are terminal (`SUCCEEDED`, `FAILED` or `CANCELLED`), deleting
the parent also deletes its run history. In the admin this cascade requires the
`inspector.delete_workspacetaskrun` permission (superusers have it); staff without it
cannot delete a parent that still has run history. Export any history you need before
confirming.
Worker publications are conditional on the running status and immutable claim start
time. Recovery retires that claim: a late worker cannot replace its logs, progress,
metadata or failure with success, and returns `{retired: true, status: ...}` when it
detects retirement. Unexpected worker errors still propagate without changing a
recovered outcome. This fence does not stop a live command; the shutdown check remains
required before recovery.

Workspace commands are checked by one guard that sees the argv list exactly as it is
executed. An argv is accepted only when it is `<interpreter> manage.py <command>` from
`INSPECTOR_SAFE_COMMANDS` followed by arguments that the command's policy in
`djdesk.inspector.command_runner` lists: app labels and the read-only options of `check`,
`showmigrations`, `diffsettings`, `sqlmigrate`, `inspectdb`, and `dumpdata` (for example
`--database`, `--format`, `--indent`, `--exclude`, `--pks`, `--deploy`, `--tag`). Everything
else is refused before a process starts, including `dumpdata --output`/`-o` in every
spelling, abbreviated or grouped options, `--settings`, `--pythonpath`, and
`diffsettings --default`. Presets therefore cannot direct writes to disk, and an allowlist
entry of any other shape never matches.

Each command runs without stdin in its own process group. When it finishes, times out, or
its log callback fails, DJDesk sends `SIGTERM` to the whole group, follows up with
`SIGKILL` after a two second grace period, closes the output pipe, and reaps the child, so
processes the command started do not outlive it. A command whose descendants keep its
output open is treated as still running until the timeout. On Windows the tree is stopped
with `taskkill /T /F`, which only finds descendants while the direct child is still
running. Known limitation: the Windows code path is not exercised by the automated tests
(they run on Linux and macOS), so process-tree cleanup there is unverified. A run whose execution fails unexpectedly (for instance while saving its log) is
recorded as failed with the original error, and that error is passed on to the task backend.

Command output is bounded by bytes, not lines. DJDesk reads at most
`DJDESK_INSPECTOR_COMMAND_OUTPUT_BYTES` (default 1 MiB) from one command, whether that is
many lines or one enormous line; a command that produces more is stopped like a timed-out
one. The log kept for a task run, including timestamps and status lines, is limited to
`DJDESK_INSPECTOR_TASK_LOG_BYTES` (default 1 MiB): a log that fits is kept whole, and only
the line that would exceed the limit truncates it, with the end of the log cut just far
enough to hold a truncation marker; the command is then stopped. If the budget cannot even
hold the first log entry, the command is not started. The log of a run that fails
unexpectedly obeys the same limit, giving up earlier output before the error line. In all
these cases the run is recorded as failed and its `command` metadata carries
`output_limited: true`, except for unexpected failures, which record the error instead.
Commands whose normal output fits the budgets behave as before.
Line framing scans only newly decoded text and joins each completed/final line once,
so newline-free output uses linear work within the same byte limit.

Inspector status and task-detail responses stream through a byte ceiling before JSON
parsing, regardless of missing or inaccurate Content-Length. The ceiling is
`DJDESK_INSPECTOR_RESPONSE_BYTES` (default 8 MiB plus 30 times the task-log budget, enough
for five logs made only of JSON-escaped control characters; raising the task-log budget
raises this default), passed to the dashboard as `data-response-bytes`; without a valid
attribute the reader falls back to 8 MiB. Overflow cancels the
read and preserves the last displayed status/log; later polls or a drawer reload can
recover with a smaller response. The drawer DOM test also covers this response boundary.

Data Lab exports exclusively claim a fresh filename on every export: the template
name, then `-2`, `-3`, and so on, skipping existing names. Existing notebooks and
manual edits are preserved. The export API returns `data_lab.selected_notebook`
with the actual filename stem, and the viewer opens that newly created export.
`data_lab.selected_notebook_url` opens the new export even when it falls outside the
bounded listing. The export form permits one in-flight POST, restores its controls
after success or failure, and never retries automatically. A notebook selected while
the POST is pending keeps the viewer; export responses do not replace polled workspace state.
A successful HTTP 201 is a receipt for an already published and synced notebook. If the
optional workspace/inventory refresh or its serialization fails, the response still
returns `data_lab.selected_notebook` and `data_lab.selected_notebook_url`. The drawer
opens that file, retains its current listing/templates and lets later status polls
refresh them. Do not repeat an export merely because its optional refresh is missing.
The listing and viewer accept only regular files directly inside the workspace export
directory. Notebook symlinks (including internal links) and symlinked workspace export
directories are refused. Where supported, directory-relative opens with no symlink following
bind these checks to the opened descriptors; other systems check file identity around the open.
Invalid UTF-8, ambiguous JSON (duplicate members at any nesting level), or invalid
viewer cell/output shapes return a bounded HTTP 422 repair page. Consumed sources and
output text must contain Unicode scalar values; escaped lone surrogates are refused.
Ordinary Unicode, including emoji, combining characters and newlines, is preserved.
Viewer admission allows at most 8 MiB of notebook JSON, 500 cells, 50 outputs per code
cell and 1 MiB of aggregate UTF-8 source/output text. Before constructing HTML, it also
checks a conservative 2 MiB fragment budget including escaped text, cell/output wrappers
and paragraph markup (even empty/stripped paragraphs count). Exceeding any limit returns
a whole-view HTTP 422 size refusal; reduce the notebook in your editor and reload.
Edit the original file and reload; the viewer preserves its bytes and escapes rendered text.
The viewer reads bytes up to the opened file's initial size plus one, comparing descriptor
size, nanosecond mtime and ctime before and after the read, before decoding UTF-8 or JSON.
A changed generation is reopened, with at most three attempts per request. Continued
in-place edits return HTTP 404 (unavailable); finish saving and reload. Stable malformed
files still return HTTP 422. Atomic editor replacements can return the complete opened
generation or retry the new one; mixed generations are refused. These checks depend on
the filesystem exposing content changes through its size or timestamps.

Notebook filename stems must match the viewer route: ASCII letters, digits, `_` and `-`,
with at least one character. Other `.ipynb` names (spaces, dots or Unicode) stay untouched
on disk and are omitted from the dashboard and status feed. Rename them to a supported
stem to view them in DJDesk.

Each inventory checks at most 512 directory entries (plus one lookahead), including
hidden or unrelated entries, and offers at most 50 notebooks sorted by filename.
The window uses the filesystem's enumeration order, then chooses its alphabetically
first 50 supported names. This is a partial inventory, not the globally first 50 files.
Up to 32 directory generations (path, device/inode, mtime and ctime) are cached per
backend process, so unchanged polls reuse the same window without enumerating history.
A generation is cached only once its timestamps are more than three seconds old, because
coarse filesystem clocks can leave them unchanged after a same-tick export; recent
directories are rescanned on every poll. A scan that overlaps a directory change is
retried (up to three attempts) before the inventory is reported as unavailable.
Only the selected files are reopened for current regular-file admission and modification
times. Directory changes invalidate the window; editing file contents does not require
a new scan. `data_lab.notebooks_incomplete` and the drawer notice indicate a limited or
unavailable inventory. All files remain untouched, and older direct viewer URLs continue
to work. Polls keep an already open notebook even if it is outside their window.
Entries whose modification time is nonfinite or cannot be displayed as a UTC date
in years 1–9999 are omitted and mark the inventory incomplete. Valid siblings remain
visible; no current or epoch time is substituted. A valid epoch timestamp is preserved.

Run the real drawer DOM regression separately with development npm dependencies and an
installed Chromium (`npm ci --ignore-scripts` in `electron/`, then `npm run test:drawer`).
Set `CHROME_EXECUTABLE` to an existing Chrome/Chromium binary if needed. It renders the
actual Django dashboard with an in-memory test database and synthetic notebooks, then
exercises deferred exports and manual selection without external network requests.

Schema polling retains the current graph, layout and viewport when the rendered schema
is unchanged. Changed schemas are staged and laid out before replacing the live view;
failed rebuilds keep the previous view and retry on the next status update, without
affecting the connection state or the other dashboard panels. Callbacks
from retired graphs cannot move or persist the current viewport.
The schema lifecycle guards run with `just electron-test`. For the real Cytoscape DOM
fixture, run `npm ci --ignore-scripts` and `npm run test:schema` in `electron/`, with
`CHROME_EXECUTABLE` pointing to an installed Chrome/Chromium if needed. It uses the
same pinned Cytoscape version as the dashboard and synthetic schema data, without
external requests; it covers repeated polls, schema changes, failed construction/layout,
retired callbacks, removals and reconstruction after an empty schema.
`npm run test:schema:headless` runs the same oracle against real Cytoscape in Node
with a synthetic container, covering graph APIs without requiring a browser. It
does not verify DOM rendering.

Exports write and fsync an owner-only (0600) `.notebook-<random>.tmp` file before
atomically publishing a hard link under the next free `.ipynb` name. Concurrent exports
never overwrite one another and polls never see a partly written export. The same opened
workspace directory is fsynced after the final link and before success is acknowledged.
Where the platform offers `F_FULLFSYNC` (macOS), both syncs also flush the drive cache;
filesystems without it fall back to plain fsync.
If that sync fails, HTTP 500 names the already published, complete notebook and reports
uncertain durability. It does not remove that export or roll back existing files. Inspect
the export directory before exporting again; a retry creates another filename. A power
loss before a successful directory sync may lose the new name. Creation,
publication and cleanup use the same opened workspace directory, even if its pathname
is replaced. A renamed directory may therefore contain the export while it is unavailable
at its former path. Ordinary failures clean up only this attempt's staging inode.
A killed process may leave a hidden `.notebook-*.tmp` file; it is never listed or reused.
With exporters stopped, inspect and remove those leftovers manually. Platforms without
directory-relative, no-follow opens and hard-link publication (including Windows) refuse
exports with HTTP 400 before writing; existing notebooks remain readable through the
portable reader. The export filesystem must support hard links; filesystem failures during
publication (no hard-link support, a directory swapped for a symlink, a failed staging write)
return an HTTP 500 JSON error and leave no partial export.

Workspace saves retry slug uniqueness collisions at most five times. SQLite admission
requires its exact single-column unique error; PostgreSQL uses SQLSTATE 23505 and the
workspace table's `inspector_workspace_slug_key` diagnostic. Other integrity errors,
including duplicate project paths, propagate immediately. PostgreSQL integration checks
use a disposable `test_<PGDATABASE>` database on an explicitly selected local server:

```bash
PGHOST=/path/to/local/socket PGPORT=5432 PGDATABASE=postgres \
  DJANGO_SETTINGS_MODULE=djdesk.settings.test_postgresql \
  uv run --with 'psycopg[binary]>=3.2' python manage.py test tests.test_workspace_slugs tests.test_content_migrations \
  tests.test_workspace_snapshot_postgresql
```

Only use a local test server whose role may create/drop test databases. Under these settings
the historical migration tests also create and drop two `test_<PGDATABASE>_migration_*`
databases, so migration 0004's PostgreSQL trimming and merge path runs. The regular
SQLite suite skips the PostgreSQL-only concurrency and repeatable-read snapshot checks; no remote
database is required.

### Python dependency security

The backend requires Django >=5.2.19,<5.3 and sqlparse >=0.6.0, including packaged installs.
Run `just audit` to audit a frozen production-only export with pip-audit 2.10.0.
The read-only quality workflow and `just ci` run the same audit and fail on findings;
no advisory ignores are used. Auditor dependencies are optional and not shipped.

### Electron security and download limits

The shell uses Electron >=45.2.3 and electron-builder >=26.18.0; the bundler's
TOML parser requires >=5.0.1. Run npm ci and npm audit in electron/ after lock changes.
The refreshed lock removes the targeted Electron and tar advisories. As of 2026-11-06,
npm audit still reports six moderate entries from builder's transitive minimatch
GHSA-g3hv-75wg-m249 (including dependent packages); no supported builder update resolves
that tree yet. No overrides or advisory ignores are used.

Interpreter archives use verified regular, non-symlink cache entries only. Cache hashing
uses bounded opened-file reads and checks identity/metadata before reuse; special,
oversized or changing cache entries are refused without changing their targets.
Cache validation shares the transfer deadline. Downloads stage beside the cache,
allow at most five HTTPS redirects and 256 MiB, and have a two minute total deadline.
Only a matching registered SHA256 is published. Failed attempts remove their own
partials and preserve the prior cache; a stale or interrupted cache is retried automatically.
Runtime builds reserve their destination, construct and verify unique sibling staging,
then promote with restoration of the old bundle on failure. See
[build and recovery details](electron/README.md#building-the-django-bundle-phase-3),
including retained backups, interrupted reservations and extraction scratch cleanup.

Node.js >=22.12.0 is required by the refreshed shell toolchain. The read-only
electron-smoke workflow builds the Python bundle and an unsigned unpacked app,
then launches the shell with an empty temporary profile under Xvfb to verify the
backend, renderer isolation and preload bridge. It publishes no artifacts. Locally,
run npm run bundle, build with electron-builder --dir --publish never, then pass the
unpacked executable to npm run smoke -- <executable> inside electron/. The smoke
uses Playwright to assert app.isPackaged and loads the real asar/preload and bundled
resources. Installers remain manual; smoke scripts are excluded from packages.
