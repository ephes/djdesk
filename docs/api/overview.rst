API Overview
============

The reference build exposes a handful of JSON endpoints that power the Electron
renderer. Every endpoint lives under the ``inspector`` namespace and requires CSRF
tokens when called from the browser; Electron injects the cookie for us.

Authentication
--------------

The reference build uses Django's default session middleware. Electron shares cookies with the embedded browser context, so CSRF tokens are available via ``document.cookie`` without additional work. If you expose the endpoints to external browsers or tools, log in through the Django admin (or your own login form) first so the session cookie exists.

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

Adding your own endpoints
-------------------------

1. Keep APIs namespaced (DJDesk uses ``inspector``) so Electron only talks to the routes you expect.
2. Return consolidated payloads where possible—``workspace_status_payload`` is an example that feeds multiple UI panels.
3. Protect mutating endpoints with CSRF. DJDesk sends ``X-CSRFToken`` headers and cookies when posting from the assistant drawer.
4. Document each endpoint’s schema inside ``docs/api/rest_endpoints.rst`` (or your equivalent) so contributors know what data Electron expects.

WebSockets vs polling
---------------------

The reference build uses polling (see ``inspector/static/inspector/app.js``) because it is easy to reason about and works before :mod:`channels` is introduced. ``docs/api/websocket_channels.rst`` outlines the desired future channels, but until that code lands you can rely on the REST responses documented here.
