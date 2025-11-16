API Overview
============

The tutorial build exposes a handful of JSON endpoints that power the Electron
renderer. Every endpoint lives under the ``inspector`` namespace and requires CSRF
tokens when called from the browser; Electron injects the cookie for us.

Authentication
--------------

Stage 0/1 rely on Django's default session middleware. Electron shares cookies with
the embedded browser context, so CSRF tokens are available via ``document.cookie``.
If you plan to open these endpoints in an external browser, log in via the Django
admin first so the session cookie exists.

Key endpoints
-------------

``GET /api/workspaces/<slug>/status/``
    Returns the scan queue, insights, schema metadata, task history, doc links, and
    log excerpts for the requested workspace. See :doc:`rest_endpoints` for the full
    schema.

``POST /api/task-runs/``
    Queues a ``django-tasks`` job using the assistant drawer form data. Responds with
    the same payload as the status endpoint so the renderer can refresh without
    waiting for the poll interval.

``GET /api/task-runs/<id>/``
    Lightweight endpoint for future explorers that want to fetch log output on demand.

WebSockets vs polling
---------------------

The tutorial build uses polling (see ``inspector/static/inspector/app.js``) because it
is easy to reason about and works while Electron is still being wired up. The API
docs describe the desired WebSocket channels, but until :mod:`channels` lands you can
rely on the REST responses documented in this chapter.
