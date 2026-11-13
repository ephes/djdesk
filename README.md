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
Each replacement is fully written beside the destination first. A write failure leaves the old
sample intact; a publication failure restores it. If restoration itself fails, the last copy
remains under `.sample-slug-*/backup` inside the sample root for manual recovery.
If cleanup fails after publication, the command reports an error with the new sample already
installed; any leftover backup may be incomplete.

The Electron docs drawer prefers a local Sphinx bundle when it exists. Run `just docs-bundle` to copy `docs/_build/html` into `var/docs_bundle/`; the drawer automatically falls back to https://djdesk.readthedocs.io/ when the offline bundle is missing.

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

The dashboard loads four recent scans and five recent task runs for the selected
workspace only, ordered newest first with the primary key breaking timestamp ties.
The workspace selector loads names and paths without historical logs or metadata;
initial task history omits log bodies. Status polling keeps the same four/five windows
and includes those task logs. Renderer status, severity and log-level classes use
supported values with neutral defaults; labels remain escaped text.

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
Use the same settings/database as those workers. The command accepts at most 100
selected running IDs, rejects missing/terminal runs, preserves logs, and marks them
failed with interruption metadata. It trusts the operator's shutdown confirmation;
it cannot detect live workers. Restart workers afterward; duplicate deliveries never
replay these commands. Any desired retry requires a new, explicitly approved request.

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

Data Lab exports exclusively claim a fresh filename on every export: the template
name, then `-2`, `-3`, and so on, skipping existing names. Existing notebooks and
manual edits are preserved. The export API returns `data_lab.selected_notebook`
with the actual filename stem, and the viewer opens that newly created export.
The listing and viewer accept only regular files directly inside the workspace export
directory. Notebook symlinks (including internal links) and symlinked workspace export
directories are refused. Where supported, directory-relative opens with no symlink following
bind these checks to the opened descriptors; other systems check file identity around the open.
Invalid UTF-8, JSON or viewer cell/output shapes return a bounded HTTP 422 repair page.
Edit the original file and reload; the viewer preserves its bytes and escapes rendered text.

Notebook filename stems must match the viewer route: ASCII letters, digits, `_` and `-`,
with at least one character. Other `.ipynb` names (spaces, dots or Unicode) stay untouched
on disk and are omitted from the dashboard and status feed. Rename them to a supported
stem to view them in DJDesk.

Exports write and flush an owner-only (0600) `.notebook-<random>.tmp` file before
atomically publishing a hard link under the next free `.ipynb` name. Concurrent exports
never overwrite one another and polls never see a partly written export. Creation,
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
  uv run --with 'psycopg[binary]>=3.2' python manage.py test tests.test_workspace_slugs
```

Only use a local test server whose role may create/drop test databases. The regular
SQLite suite skips the PostgreSQL-only concurrency check; no remote database is required.

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

Interpreter archives use verified cache entries only. Downloads stage beside the cache,
allow at most five HTTPS redirects and 256 MiB, and have a two minute total deadline.
Only a matching registered SHA256 is published. Failed attempts remove their own
partials and preserve the prior cache; a stale or interrupted cache is retried automatically.

Node.js >=22.12.0 is required by the refreshed shell toolchain. The read-only
electron-smoke workflow builds the Python bundle and an unsigned unpacked app,
then launches the shell with an empty temporary profile under Xvfb to verify the
backend, renderer isolation and preload bridge. It publishes no artifacts. Locally,
run npm run bundle, build with electron-builder --dir --publish never, then pass the
unpacked executable to npm run smoke -- <executable> inside electron/. The smoke
uses Playwright to assert app.isPackaged and loads the real asar/preload and bundled
resources. Installers remain manual; smoke scripts are excluded from packages.
