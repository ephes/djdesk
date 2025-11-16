Electron Packaging
==================

DJDesk includes a full Electron wrapper so the tutorial can be shipped as an
installable desktop app. This page explains **why** we bundle Django with Electron,
how the local workflow operates, and how GitHub Actions produces distributable
installers.

Deciding on Electron
--------------------

Before diving into commands, confirm that Electron is the right transport for your
project.

Benefits
~~~~~~~~

* **Zero Python setup for end users.** Every installer contains python-build-standalone
  Python 3.14.0+20251031 plus the packages listed under
  ``[project].dependencies`` in ``pyproject.toml``.
* **Offline-first experience.** ``electron/django-bundle/`` ships Django, collected
  static files, and project sources, so the UI runs without network access after the
  first install.
* **Native presentation.** Electron Builder emits DMG/ZIP (macOS), NSIS/ZIP (Windows),
  and AppImage/DEB/TAR.GZ (Linux), giving users familiar installers and dock/taskbar
  integration.

Trade-offs
~~~~~~~~~~

* Installer size quickly exceeds 150 MB once Django, Electron, and optional
  data-science libraries are included.
* Memory footprint includes both the Chromium renderer and the Django/Python process.
* Updates are manual today—users must install new builds until auto-update code is
  added.
* The bundled Django server only listens on ``127.0.0.1``. Multi-user or remote access
  still requires a traditional deployment.

When to use it
~~~~~~~~~~~~~~

* Internal tooling for analysts or operators who cannot manage Python environments.
* Offline-friendly demos where bandwidth is unpredictable.
* Prototyping native-feeling UX over existing Django dashboards or admin.

When to reconsider
~~~~~~~~~~~~~~~~~~

* Strict bundle size limits (<50 MB) or highly resource-constrained targets.
* Deployments that already succeed as web apps or need centralized databases.
* Teams preferring lighter shells (e.g., Tauri) or Python-only bundling (PyInstaller).

System overview
---------------

The moving pieces can be summarized as:

.. code-block:: text

   Electron main process
         │
         │ launches bundled python or system python
         ▼
   run_django.py ──> manage.py runserver (Django)
                        │
                        └─ serves http://127.0.0.1:<port> to the renderer window

``electron/main.js`` chooses the interpreter, waits for Django to boot, and points the
renderer at the local server.

Local workflow
--------------

1. **Install Node dependencies**

   ``just electron-install`` (runs ``npm install`` inside ``electron/``).

2. **Bundle Django (optional during initial prototyping)**

   ``just electron-bundle`` (calls ``npm run bundle``) to create
   ``electron/django-bundle/``. Without this directory the app falls back to the system
   Python.

3. **Start the shell**

   ``just electron-start`` which executes ``npm start``. ``main.js`` looks for
   ``electron/django-bundle/python/bin/python3`` on macOS/Linux or
   ``electron/django-bundle/python/python.exe`` on Windows. If neither exists it tries
   ``python3.14``, ``python3``, or ``python`` from your PATH. Override the interpreter
   via ``PYTHON=/custom/python npm start`` when needed.

Bundler internals
-----------------

``npm run bundle`` (or ``just electron-bundle``) executes ``electron/build-django.js``.
The script produces a reproducible Python payload in seven stages:

1. Delete and recreate ``electron/django-bundle/``.
2. Download the matching python-build-standalone archive for the current OS/arch,
   verify its SHA256 checksum, and unpack it into ``django-bundle/python/``.
3. Install every dependency listed under ``[project].dependencies`` using
   ``python -m pip install --no-cache-dir …`` inside the bundled interpreter. Optional
   extras/dev dependencies are ignored.
4. Copy ``manage.py`` plus ``src/djdesk`` (excluding ``__pycache__``) into
   ``django-bundle/src/``.
5. Run ``python manage.py collectstatic --no-input --clear`` with
   ``DJANGO_STATIC_ROOT`` pointing to ``django-bundle/staticfiles/``.
6. Write ``run_django.py`` (the in-bundle launcher) and a ``VERSION`` file containing
   ``git rev-parse --short HEAD`` plus a timestamp.
7. Import Django using the bundled interpreter to prove the environment is healthy.

Interpreter downloads are cached under ``electron/.python-downloads/`` so subsequent
bundles reuse existing archives.

Bundle layout
-------------

.. code-block:: text

   electron/django-bundle/
   ├── manage.py
   ├── run_django.py
   ├── src/djdesk/…
   ├── staticfiles/
   ├── python/
   └── VERSION

``run_django.py`` augments ``PYTHONPATH`` so ``django-bundle/src`` takes precedence,
sets ``DJANGO_ENV``/``DJANGO_SETTINGS_MODULE`` defaults, and then executes
``manage.py runserver --noreload`` on the host/port provided by Electron.

Runtime behavior
----------------

``electron/main.js`` controls application startup:

* Uses ``get-port`` to find an available port (prefers ``8000``).
* Calls ``resolvePython`` to prioritize the bundled interpreter and only uses the
  system Python when necessary.
* Spawns Django via ``run_django.py`` when a bundle exists, or directly runs
  ``manage.py runserver`` otherwise.
* Sets ``DJANGO_ENV=local`` and ``DJANGO_SETTINGS_MODULE=djdesk.settings.local`` unless
  they are already defined.
* Polls ``http://127.0.0.1:<port>/`` up to 30 times before showing an error, ensuring
  the renderer window connects only after Django responds.
* Opens DevTools automatically when ``NODE_ENV=development``.

Building platform installers
----------------------------

``npm run build`` (surfaced via ``just electron-build-*``) regenerates the bundle and
then invokes Electron Builder using ``electron/electron-builder.json``. Platform
outputs are:

* **macOS** – DMG + ZIP
* **Windows** – NSIS installer + ZIP
* **Linux** – AppImage, DEB, and TAR.GZ

Electron Builder excludes ``django-bundle/`` from the application ASAR but re-adds it
through ``extraResources`` so installers always contain the Python runtime and Django
payload alongside ``resources/app.asar``.

Production checklist
--------------------

* **Database location.** ``djdesk.settings.base`` points SQLite at ``BASE_DIR /
  "db.sqlite3"``. Inside an installer this resolves to ``django-bundle/db.sqlite3``,
  which may be read-only. Override ``DATABASES['default']['NAME']`` via environment
  variables to a writable path under ``app.getPath('userData')``.
* **Migrations.** ``build-django.js`` does not run ``manage.py migrate``. Run migrations
  manually before bundling or extend the script to apply them so new installs ship with
  initialized schemas.
* **Updates.** Releases are manual (no auto-updater yet). ``just electron-workflow-run``
  triggers CI builds, but you still need to distribute the resulting artifacts.
* **Logging & diagnostics.** ``main.js`` streams Django stdout/stderr to the terminal.
  For production telemetry, capture logs under ``app.getPath('logs')`` or integrate a
  logging service.
* **Secrets.** ``DJANGO_SECRET_KEY`` defaults to a development value. Set it during
  bundling via environment variables if you need unique secrets per build.
* **Bundle size.** Run ``du -sh electron/django-bundle`` and ``du -sh electron/dist`` to
  understand disk usage before shipping.

Automation & CI
---------------

``.github/workflows/electron-desktop.yml`` builds installers for macOS, Linux, and
Windows:

1. Jobs run on ``macos-latest``, ``ubuntu-latest``, and ``windows-latest``.
2. ``actions/setup-node`` installs Node.js 22 with npm caching.
3. ``astral-sh/setup-uv`` provides the ``uv`` CLI required by ``build-django.js``.
4. Ubuntu installs ``libfuse2`` so AppImage packaging succeeds.
5. ``npm ci`` installs dependencies in ``electron/``.
6. ``npm run bundle`` creates ``electron/django-bundle/`` (``npm run build`` runs
   ``bundle`` again; the duplication is safe but could be optimized later).
7. ``npm run build -- --<platform>`` runs Electron Builder, which copies
   ``django-bundle/`` into each installer.
8. ``actions/upload-artifact`` publishes ``djdesk-macos``, ``djdesk-linux``, and
   ``djdesk-windows`` packages from ``electron/dist/``.

The workflow is currently **manual-only** (trigger: ``workflow_dispatch``). Launch it
via the GitHub UI or run ``just electron-workflow-run`` locally. To build on pushes,
extend the ``on:`` block with the desired paths.

Alternatives
------------

.. list-table::
   :header-rows: 1

   * - Approach
     - Typical bundle size
     - Complexity
     - Best for
   * - Electron (DJDesk)
     - 150 MB+
     - Moderate
     - Django apps needing desktop UX + offline capability
   * - PyInstaller / Nuitka
     - 40 MB+
     - Low–Moderate
     - Pure Python CLIs or GUIs without an embedded browser
   * - Tauri
     - 20 MB+
     - High (Rust toolchain)
     - Lightweight web UIs with tight bundle-size budgets
   * - Traditional web deploy
     - N/A
     - Low
     - Multi-user or internet-facing deployments

Adding or updating dependencies
-------------------------------

Edit ``[project].dependencies`` in ``pyproject.toml`` and rerun
``just electron-bundle``. Because dependencies install via ``pip`` inside the bundled
Python 3.14 interpreter, ensure wheels exist for every target platform. Packages with
native extensions (NumPy, pandas, etc.) inflate both bundle size and build time but
otherwise require no code changes—``npm run build`` will carry the updated bundle into
the next set of installers.

Utility recipes
---------------

* ``just electron-runs`` – list recent GitHub Actions run history.
* ``just electron-download-latest`` – download the newest successful artifacts into
  ``dist-artifacts/`` (requires GitHub CLI).
* ``just electron-clean-artifacts`` – remove downloaded artifacts.

Run ``just -l`` to explore every helper available in the repository.
