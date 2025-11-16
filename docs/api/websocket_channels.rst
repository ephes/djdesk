WebSocket Channels
==================

The current reference build relies on REST polling, but the PRD calls out WebSockets for live updates. This section documents the planned channels so contributors can help implement them later.

Planned channels
----------------

``ws://localhost:<port>/ws/workspaces/<slug>/``
    Streams scan queue events, insight updates, and log entries as they happen. Each
    message will reuse the structure from the REST status endpoint so the renderer
    can swap between polling and WebSockets without touching UI code.

``ws://localhost:<port>/ws/task-runs/<workspace>/``
    Sends task progress deltas. Messages include ``id``, ``status``, ``progress``,
    ``log_line`` and ``timestamp`` so the assistant drawer can append to the log
    stream and trigger notifications immediately.

Until these channels exist, ``inspector/static/inspector/app.js`` polls every six
seconds. The code is structured so the WebSocket handlers can reuse
``updateFromPayload`` once we flip the switch.
