Stage 0 — Getting Started
=========================

Stage 0 proves the plumbing: Electron boots Django, loads the inspector shell, and
confirms the local bundle can serve static assets without touching production.

Prerequisites
-------------

* Python 3.14+ with ``uv`` available on your ``PATH``
* Node 20+ for the Electron wrapper
* ``just`` (optional, but the docs assume its recipes)

Install and launch
------------------

.. code-block:: bash

    just install            # uv sync + editable djdesk install
    just electron-install   # npm install inside ./electron
    just electron-start     # starts Django + Electron together

By default ``DJANGO_SETTINGS_MODULE`` resolves to ``djdesk.settings.local`` so you
get debug tooling and the seeded tutorial workspace. ``npm start`` watches the Django
process and will restart the shell if you change backend code.

Smoke test
----------

When the window appears you should see:

* The Convexity-inspired chrome with toolbar, rails, and the sample "Atlas Telemetry"
  workspace.
* Live polling against ``/api/workspaces/<slug>/status/`` (visible in DevTools if you
  open the network tab).
* ``uv`` logs in the terminal showing ``runserver`` bound to an ephemeral port chosen
  by the Electron bootstrapper.

Troubleshooting
---------------

* **Port conflicts** — the Electron launcher uses :mod:`get-port` to locate an open
  port. If you want to pin a port, set ``DJDESK_FORCE_PORT=8787`` before running
  ``npm start``.
* **Firewall dialogs** — grant access to ``python3`` the first time macOS asks so
  Electron can proxy the Django server.
* **Stuck window** — if the Electron window is blank, press ``Cmd+Alt+I`` to open
  DevTools and check for failed requests. ``Cmd+R`` reloads the renderer without
  killing Django.

Once Stage 0 is reliable you can move on to Stage 1 (workspace import) without
touching any feature flags.
